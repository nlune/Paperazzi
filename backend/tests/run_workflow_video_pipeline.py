from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PIL import Image
except ModuleNotFoundError:  # pragma: no cover - optional at runtime
    Image = None  # type: ignore[assignment]

try:
    from gradium.client import GradiumClient
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    GradiumClient = None  # type: ignore[assignment]

from src.config import get_settings
from src.services.text_tokens import normalize_token, tokenize_words


PRIMITIVE_CYCLE = (
    "page_zoom_pan",
    "text_highlight",
    "figure_callout",
    "equation_steps",
    "split_explain",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consume workflow JSON from the Gemini section test, synthesize section "
            "narration with Gradium, build cumulative word timings, and render a "
            "final Revideo MP4."
        )
    )
    parser.add_argument(
        "workflow_json",
        nargs="*",
        help="One or more workflow JSON files from run_gemini_workflow_test.py",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "test_outputs"),
        help="Directory for timings, audio, scene data, and final MP4.",
    )
    parser.add_argument(
        "--render-timeout-s",
        type=int,
        default=900,
        help="Timeout for npm render process.",
    )
    return parser.parse_args()


def _default_workflow_jsons() -> list[Path]:
    candidates = sorted(ROOT.glob("test_outputs/gemini_workflow*/gemini_workflow_page-*.json"))
    if not candidates:
        raise SystemExit(
            "No workflow JSONs found. Run tests/run_gemini_workflow_test.py first."
        )
    return candidates


def _word_timings_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    word_timings: list[dict[str, Any]] = []
    occurrence_by_word: dict[str, int] = {}
    for segment in segments:
        segment_text = str(segment.get("text", ""))
        start_s = float(segment.get("start_s", 0.0))
        stop_s = float(segment.get("stop_s", start_s))
        tokens = tokenize_words(segment_text)
        if not tokens:
            continue
        duration = max(0.001, stop_s - start_s)
        token_duration = duration / len(tokens)
        for index, token in enumerate(tokens):
            token_start = start_s + index * token_duration
            token_stop = stop_s if index == len(tokens) - 1 else token_start + token_duration
            normalized = normalize_token(token)
            occurrence_by_word[normalized] = occurrence_by_word.get(normalized, 0) + 1
            word_timings.append(
                {
                    "word": token,
                    "normalized_word": normalized,
                    "occurrence": occurrence_by_word[normalized],
                    "start_s": token_start,
                    "stop_s": token_stop,
                }
            )
    return word_timings


async def _synthesize_sections_with_gradium(
    *,
    sections: list[dict[str, Any]],
    base_url: str,
    voice_id: str | None,
) -> tuple[bytes, dict[str, list[dict[str, Any]]]]:
    if GradiumClient is None:
        raise SystemExit("gradium package is not installed in this Python environment.")
    client = GradiumClient(base_url=base_url)
    audio_chunks: list[bytes] = []
    segments_by_section: dict[str, list[dict[str, Any]]] = {
        section["section_key"]: [] for section in sections
    }
    all_text_segments: list[dict[str, Any]] = []

    async with client.tts_realtime(
        model_name="default",
        voice_id=voice_id,
        output_format="wav",
        wait_for_ready_on_start=True,
    ) as tts:
        for section in sections:
            await tts.send_text(
                section["narration_text"],
                client_req_id=section["section_key"],
            )
        await tts.send_eos()

        async for message in tts:
            msg_type = message.get("type")
            if msg_type == "audio":
                audio_chunks.append(message["audio"])
            elif msg_type == "text":
                section_key = str(message.get("client_req_id") or "")
                segment = {
                    "text": message.get("text", ""),
                    "start_s": float(message.get("start_s", 0.0)),
                    "stop_s": float(
                        message.get("stop_s", message.get("start_s", 0.0))
                    ),
                }
                all_text_segments.append(segment)
                if section_key in segments_by_section:
                    segments_by_section[section_key].append(segment)

    if any(segments for segments in segments_by_section.values()):
        return b"".join(audio_chunks), segments_by_section

    segment_cursor = 0
    for section in sections:
        expected_token_count = len(tokenize_words(section["narration_text"]))
        assigned_token_count = 0
        assigned_segments: list[dict[str, Any]] = []
        while (
            segment_cursor < len(all_text_segments)
            and assigned_token_count < expected_token_count
        ):
            segment = all_text_segments[segment_cursor]
            assigned_segments.append(segment)
            assigned_token_count += len(tokenize_words(str(segment.get("text", ""))))
            segment_cursor += 1
        segments_by_section[section["section_key"]] = assigned_segments

    if segment_cursor < len(all_text_segments) and sections:
        segments_by_section[sections[-1]["section_key"]].extend(
            all_text_segments[segment_cursor:]
        )

    return b"".join(audio_chunks), segments_by_section


def _stream_render_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_s: int,
) -> None:
    with subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as process:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                print(line.rstrip(), flush=True)
            code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise SystemExit(
                f"Render timeout exceeded ({timeout_s}s). Process killed."
            ) from exc
    if code != 0:
        raise SystemExit(f"Render process failed with exit code {code}.")


def _run_ffmpeg_mux(
    *,
    silent_video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(silent_video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            "ffmpeg mux failed:\n" + completed.stdout[-4000:]
        )


def _image_size(image_path: Path) -> dict[str, int]:
    if Image is None:
        raise SystemExit("Pillow is required for workflow video rendering.")
    with Image.open(image_path) as image:
        width, height = image.size
    return {"width": int(width), "height": int(height)}


def _image_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime_type = "image/png"
    if suffix in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _coerce_narration_anchor(
    *,
    narration_link: dict[str, Any],
    fallback_word: str,
    fallback_occurrence: int,
) -> tuple[str, int, str | None]:
    raw_word = narration_link.get("narration_word")
    raw_occurrence = narration_link.get("narration_occurrence")

    narration_word = str(raw_word).strip() if raw_word is not None else ""
    warning_parts: list[str] = []
    if not narration_word:
        narration_word = fallback_word
        warning_parts.append(f"missing narration_word; using pdf word {fallback_word!r}")

    try:
        narration_occurrence = int(raw_occurrence)
    except (TypeError, ValueError):
        narration_occurrence = max(1, int(fallback_occurrence))
        warning_parts.append(
            f"missing narration_occurrence; using pdf occurrence {narration_occurrence}"
        )
    else:
        if narration_occurrence < 1:
            narration_occurrence = max(1, int(fallback_occurrence))
            warning_parts.append(
                f"invalid narration_occurrence; using pdf occurrence {narration_occurrence}"
            )

    warning = "; ".join(warning_parts) if warning_parts else None
    return narration_word, narration_occurrence, warning


def _load_sections_from_workflow(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    page_image_path = Path(payload["page_image_path"]).resolve()
    image_size = _image_size(page_image_path)
    image_data_url = _image_data_url(page_image_path)
    sections: list[dict[str, Any]] = []

    for index, section in enumerate(payload.get("processed_sections", []), start=1):
        decision = section.get("decision", {})
        if not decision.get("use_section", False):
            continue
        highlight_words = section.get("highlight_words", [])
        highlight_bboxes = section.get("highlight_bboxes", [])
        actions = section.get("actions", [])
        narration_links = section.get("narration_highlight_links", [])
        narration_text = str(section.get("narration_text", "")).strip()
        if not narration_text:
            raise SystemExit(f"{path}: {section['section_id']} has no narration_text.")
        workflow_unresolved = [
            str(item).strip()
            for item in section.get("unresolved", [])
            if str(item).strip()
        ]
        counts = {
            "highlight_words": len(highlight_words),
            "highlight_bboxes": len(highlight_bboxes),
            "actions": len(actions),
            "narration_highlight_links": len(narration_links),
        }
        if len(set(counts.values())) != 1:
            raise SystemExit(
                f"{path}: {section['section_id']} has misaligned lists: {counts}"
            )

        by_pdf_key: dict[tuple[str, int], dict[str, Any]] = {}
        for item in highlight_words:
            key = (normalize_token(item["pdf_word"]), int(item["pdf_occurrence"]))
            by_pdf_key.setdefault(key, {}).update({"highlight_word": item})
        for item in highlight_bboxes:
            key = (normalize_token(item["pdf_word"]), int(item["pdf_occurrence"]))
            by_pdf_key.setdefault(key, {}).update({"highlight_bbox": item})
        for item in actions:
            key = (normalize_token(item["pdf_word"]), int(item["pdf_occurrence"]))
            by_pdf_key.setdefault(key, {}).update({"action": item})
        for item in narration_links:
            key = (normalize_token(item["pdf_word"]), int(item["pdf_occurrence"]))
            by_pdf_key.setdefault(key, {}).update({"narration_link": item})

        ordered_actions: list[dict[str, Any]] = []
        for action_order, item in enumerate(highlight_words, start=1):
            key = (normalize_token(item["pdf_word"]), int(item["pdf_occurrence"]))
            bundle = by_pdf_key.get(key, {})
            if {
                "highlight_word",
                "highlight_bbox",
                "action",
                "narration_link",
            } - set(bundle):
                raise SystemExit(
                    f"{path}: {section['section_id']} missing source/bbox/action/narration data for {key}"
                )
            narration_word, narration_occurrence, narration_warning = _coerce_narration_anchor(
                narration_link=bundle["narration_link"],
                fallback_word=str(bundle["highlight_word"]["pdf_word"]),
                fallback_occurrence=int(bundle["highlight_word"]["pdf_occurrence"]),
            )
            if narration_warning:
                workflow_unresolved.append(
                    f"{section['section_id']} {bundle['highlight_word']['pdf_word']}"
                    f"#{bundle['highlight_word']['pdf_occurrence']}: {narration_warning}."
                )
            ordered_actions.append(
                {
                    "order": action_order,
                    "pdf_word": bundle["highlight_word"]["pdf_word"],
                    "pdf_occurrence": int(bundle["highlight_word"]["pdf_occurrence"]),
                    "source_text": bundle["action"]["action"],
                    "bbox_norm": bundle["highlight_bbox"]["bbox_norm"],
                    "narration_word": narration_word,
                    "narration_occurrence": narration_occurrence,
                }
            )

        sections.append(
            {
                "workflow_json_path": str(path.resolve()),
                "section_key": f"{path.stem}:{section['section_id']}:{index:02d}",
                "section_id": section["section_id"],
                "section_title": section["section_title"],
                "page": int(section["page"]),
                "page_image_path": str(page_image_path),
                "page_image_src": image_data_url,
                "image_size": image_size,
                "narration_text": narration_text,
                "ordered_actions": ordered_actions,
                "workflow_unresolved": workflow_unresolved,
            }
        )

    return sections


def _resolve_timed_action(
    *,
    action: dict[str, Any],
    word_timings: list[dict[str, Any]],
    primitive: str,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = normalize_token(action["narration_word"])
    same_word = [
        item for item in word_timings if item["normalized_word"] == normalized
    ]
    if not same_word:
        return None, (
            f"Missing narration timing for {action['narration_word']!r} in section "
            f"{action['section_id']}."
        )
    index = (max(1, int(action["narration_occurrence"])) - 1) % len(same_word)
    chosen = same_word[index]
    warning = None
    if chosen["occurrence"] != int(action["narration_occurrence"]):
        warning = (
            f"{action['section_id']} {action['pdf_word']}#{action['pdf_occurrence']}: "
            f"narration occurrence {action['narration_occurrence']} was out of bounds; "
            f"used occurrence {chosen['occurrence']} via modulo wrap."
        )
    return (
        {
            "section_id": action["section_id"],
            "section_title": action["section_title"],
            "page": action["page"],
            "word": action["pdf_word"],
            "occurrence": int(action["pdf_occurrence"]),
            "primitive": primitive,
            "start_s": float(chosen["start_s"]),
            "stop_s": float(chosen["stop_s"]),
            "bbox_norm": action["bbox_norm"],
            "action_text": action["source_text"],
            "narration_word": chosen["word"],
            "narration_occurrence": int(chosen["occurrence"]),
        },
        warning,
    )


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    if "GRADIUM_API_KEY" not in os.environ:
        raise SystemExit("GRADIUM_API_KEY is required.")

    workflow_paths = (
        [Path(item).expanduser().resolve() for item in args.workflow_json]
        if args.workflow_json
        else _default_workflow_jsons()
    )
    missing = [path for path in workflow_paths if not path.exists()]
    if missing:
        raise SystemExit("Missing workflow jsons: " + ", ".join(str(path) for path in missing))

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    sections: list[dict[str, Any]] = []
    for path in workflow_paths:
        sections.extend(_load_sections_from_workflow(path))
    if not sections:
        raise SystemExit("No accepted sections found in workflow JSON inputs.")

    sections.sort(key=lambda item: (item["page"], item["section_id"]))

    audio_bytes, segments_by_section = asyncio.run(
        _synthesize_sections_with_gradium(
            sections=sections,
            base_url=os.getenv("GRADIUM_BASE_URL", settings.gradium_base_url),
            voice_id=os.getenv("GRADIUM_VOICE_ID", settings.gradium_voice_id),
        )
    )
    if not audio_bytes:
        raise SystemExit("Gradium returned empty audio bytes for workflow render.")

    final_audio_path = output_dir / "workflow_video_audio.wav"
    final_audio_path.write_bytes(audio_bytes)

    manifest_sections: list[dict[str, Any]] = []
    all_timed_actions: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for section_index, section in enumerate(sections, start=1):
        section_segments = segments_by_section.get(section["section_key"], [])
        if not section_segments:
            raise SystemExit(f"No Gradium segments returned for {section['section_key']}.")
        word_timings = _word_timings_from_segments(section_segments)
        timed_actions: list[dict[str, Any]] = []
        for action_index, action in enumerate(section["ordered_actions"], start=1):
            enriched_action = {
                **action,
                "section_id": section["section_id"],
                "section_title": section["section_title"],
                "page": section["page"],
            }
            primitive = PRIMITIVE_CYCLE[(action_index - 1) % len(PRIMITIVE_CYCLE)]
            timed_action, warning = _resolve_timed_action(
                action=enriched_action,
                word_timings=word_timings,
                primitive=primitive,
            )
            if warning:
                unresolved.append(warning)
            if timed_action is None:
                unresolved.append(
                    f"Could not resolve timed action for {section['section_id']} "
                    f"{action['pdf_word']}#{action['pdf_occurrence']}."
                )
                continue
            timed_action["action_id"] = f"section-{section_index:02d}-action-{action_index:02d}"
            timed_actions.append(timed_action)
            all_timed_actions.append(timed_action)

        section_start_s = min(segment["start_s"] for segment in section_segments)
        section_stop_s = max(segment["stop_s"] for segment in section_segments)
        manifest_sections.append(
            {
                "section_id": section["section_id"],
                "section_title": section["section_title"],
                "page": section["page"],
                "page_image_path": section["page_image_path"],
                "image_size": section["image_size"],
                "start_s": section_start_s,
                "stop_s": section_stop_s,
                "narration_text": section["narration_text"],
                "segments": section_segments,
                "word_timings": word_timings,
                "timed_actions": timed_actions,
                "workflow_json_path": section["workflow_json_path"],
            }
        )

    if not all_timed_actions:
        raise SystemExit("No timed actions resolved from workflow JSON inputs.")

    frame_width = max(section["image_size"]["width"] for section in manifest_sections)
    frame_height = max(section["image_size"]["height"] for section in manifest_sections)
    duration_s = max(section["stop_s"] for section in manifest_sections)

    scene_data = {
        "final_audio_path": str(final_audio_path.resolve()),
        "duration_s": duration_s,
        "frame_size": {"width": frame_width, "height": frame_height},
        "sections": manifest_sections,
        "timed_actions": all_timed_actions,
        "unresolved": unresolved,
    }
    scene_data_path = output_dir / "workflow_video_scene_data.json"
    scene_data_path.write_text(
        json.dumps(scene_data, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    timings_summary_path = output_dir / "workflow_video_timing_summary.json"
    timings_summary_path.write_text(
        json.dumps(
            {
                "section_count": len(manifest_sections),
                "action_count": len(all_timed_actions),
                "duration_s": duration_s,
                "unresolved": unresolved,
                "sections": [
                    {
                        "section_id": section["section_id"],
                        "page": section["page"],
                        "start_s": section["start_s"],
                        "stop_s": section["stop_s"],
                        "action_count": len(section["timed_actions"]),
                    }
                    for section in manifest_sections
                ],
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    silent_video_path = output_dir / "workflow_video_silent.mp4"
    final_video_path = output_dir / "workflow_video_final.mp4"
    env = os.environ.copy()
    env["PAPERAZZI_REVIDEO_SCENE_DATA"] = str(scene_data_path.resolve())
    env["PAPERAZZI_REVIDEO_OUTPUT"] = str(silent_video_path.resolve())
    env["PAPERAZZI_REVIDEO_INCLUDE_AUDIO"] = "0"

    _stream_render_process(
        ["npm", "run", "render"],
        cwd=REPO_ROOT / "revideo",
        env=env,
        timeout_s=args.render_timeout_s,
    )
    _run_ffmpeg_mux(
        silent_video_path=silent_video_path,
        audio_path=final_audio_path,
        output_path=final_video_path,
    )

    print(
        json.dumps(
            {
                "scene_data_path": str(scene_data_path.resolve()),
                "timings_summary_path": str(timings_summary_path.resolve()),
                "audio_path": str(final_audio_path.resolve()),
                "silent_video_path": str(silent_video_path.resolve()),
                "video_path": str(final_video_path.resolve()),
                "section_count": len(manifest_sections),
                "action_count": len(all_timed_actions),
                "unresolved_count": len(unresolved),
            },
            indent=2,
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PIL import Image
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    raise SystemExit("Pillow is required for canvas video rendering.") from exc

try:
    from gradium.client import GradiumClient
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    GradiumClient = None  # type: ignore[assignment]

from src.config import get_settings
from src.services.canvas_video_renderer import render_scene_data_to_mp4
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
            "narration with Gradium, build timed canvas actions, and render a final MP4."
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
        "--fps",
        type=int,
        default=24,
        help="Frames per second for the canvas renderer.",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help="Render a single selected page from workflow JSON inputs.",
    )
    return parser.parse_args()


def _default_workflow_jsons() -> list[Path]:
    candidates = sorted(ROOT.glob("test_outputs/gemini_workflow*/gemini_workflow_page-*.json"))
    if not candidates:
        raise SystemExit("No workflow JSONs found. Run tests/run_gemini_workflow_test.py first.")
    return candidates


def _word_timings_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    word_timings: list[dict[str, Any]] = []
    occurrence_by_word: dict[str, int] = {}
    for segment in segments:
        start_s = float(segment.get("start_s", 0.0))
        stop_s = float(segment.get("stop_s", start_s))
        tokens = tokenize_words(str(segment.get("text", "")))
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
            await tts.send_text(section["narration_text"], client_req_id=section["section_key"])
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
                    "stop_s": float(message.get("stop_s", message.get("start_s", 0.0))),
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
        while segment_cursor < len(all_text_segments) and assigned_token_count < expected_token_count:
            segment = all_text_segments[segment_cursor]
            assigned_segments.append(segment)
            assigned_token_count += len(tokenize_words(str(segment.get("text", ""))))
            segment_cursor += 1
        segments_by_section[section["section_key"]] = assigned_segments

    if segment_cursor < len(all_text_segments) and sections:
        segments_by_section[sections[-1]["section_key"]].extend(all_text_segments[segment_cursor:])

    return b"".join(audio_chunks), segments_by_section


def _image_size(image_path: Path) -> dict[str, int]:
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
        counts = {
            "highlight_words": len(highlight_words),
            "highlight_bboxes": len(highlight_bboxes),
            "actions": len(actions),
            "narration_highlight_links": len(narration_links),
        }
        if len(set(counts.values())) != 1:
            raise SystemExit(f"{path}: {section['section_id']} has misaligned lists: {counts}")

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
            if {"highlight_word", "highlight_bbox", "action", "narration_link"} - set(bundle):
                raise SystemExit(
                    f"{path}: {section['section_id']} missing source/bbox/action/narration data for {key}"
                )
            ordered_actions.append(
                {
                    "order": action_order,
                    "pdf_word": bundle["highlight_word"]["pdf_word"],
                    "pdf_occurrence": int(bundle["highlight_word"]["pdf_occurrence"]),
                    "source_text": bundle["action"]["action"],
                    "primitive": str(bundle["action"].get("primitive", "text_highlight")),
                    "bbox_norm": bundle["highlight_bbox"]["bbox_norm"],
                    "narration_word": bundle["narration_link"]["narration_word"],
                    "narration_occurrence": int(bundle["narration_link"]["narration_occurrence"]),
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
            }
        )

    return sections


def _filter_single_page_sections(
    sections: list[dict[str, Any]],
    *,
    selected_page: int | None,
) -> list[dict[str, Any]]:
    if not sections:
        return sections
    available_pages = sorted({int(section["page"]) for section in sections})
    target_page = selected_page if selected_page is not None else available_pages[0]
    if target_page not in available_pages:
        raise SystemExit(
            f"Requested page {target_page} is not available in workflow inputs; "
            f"available={available_pages}"
        )
    page_sections = [section for section in sections if int(section["page"]) == target_page]
    if not page_sections:
        return []

    by_workflow: dict[str, list[dict[str, Any]]] = {}
    for section in page_sections:
        by_workflow.setdefault(str(section["workflow_json_path"]), []).append(section)

    selected_workflow_path = max(
        by_workflow,
        key=lambda item: Path(item).stat().st_mtime,
    )
    return by_workflow[selected_workflow_path]


def _resolve_timed_action(
    *,
    action: dict[str, Any],
    word_timings: list[dict[str, Any]],
    primitive: str,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = normalize_token(action["narration_word"])
    same_word = [item for item in word_timings if item["normalized_word"] == normalized]
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


def _section_focus_bbox_norm(timed_actions: list[dict[str, Any]]) -> dict[str, float]:
    if not timed_actions:
        return {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    x0 = min(float(item["bbox_norm"]["x"]) for item in timed_actions)
    y0 = min(float(item["bbox_norm"]["y"]) for item in timed_actions)
    x1 = max(float(item["bbox_norm"]["x"]) + float(item["bbox_norm"]["w"]) for item in timed_actions)
    y1 = max(float(item["bbox_norm"]["y"]) + float(item["bbox_norm"]["h"]) for item in timed_actions)
    pad_x = 0.05
    pad_y = 0.08
    x0 = max(0.0, x0 - pad_x)
    y0 = max(0.0, y0 - pad_y)
    x1 = min(1.0, x1 + pad_x)
    y1 = min(1.0, y1 + pad_y)
    return {
        "x": x0,
        "y": y0,
        "w": max(0.01, x1 - x0),
        "h": max(0.01, y1 - y0),
    }


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
    sections = _filter_single_page_sections(sections, selected_page=args.page)
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

    final_audio_path = output_dir / "workflow_canvas_video_audio.wav"
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
            primitive = str(action.get("primitive") or PRIMITIVE_CYCLE[(action_index - 1) % len(PRIMITIVE_CYCLE)])
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
                "focus_bbox_norm": _section_focus_bbox_norm(timed_actions),
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
        "page": int(sections[0]["page"]),
        "sections": manifest_sections,
        "timed_actions": all_timed_actions,
        "unresolved": unresolved,
    }
    scene_data_path = output_dir / "workflow_canvas_video_scene_data.json"
    scene_data_path.write_text(
        json.dumps(scene_data, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    final_video_path = output_dir / "workflow_canvas_video_final.mp4"
    render_scene_data_to_mp4(
        scene_data=scene_data,
        output_path=final_video_path,
        fps=args.fps,
    )

    result_summary_path = output_dir / "workflow_canvas_video_summary.json"
    result_summary_path.write_text(
        json.dumps(
            {
                "scene_data_path": str(scene_data_path.resolve()),
                "audio_path": str(final_audio_path.resolve()),
                "video_path": str(final_video_path.resolve()),
                "section_count": len(manifest_sections),
                "action_count": len(all_timed_actions),
                "duration_s": duration_s,
                "unresolved": unresolved,
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    print(f"Saved scene data: {scene_data_path}")
    print(f"Saved audio: {final_audio_path}")
    print(f"Saved final video: {final_video_path}")


if __name__ == "__main__":
    main()

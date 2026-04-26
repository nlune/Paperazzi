from __future__ import annotations

import asyncio
import base64
import json
import re
import wave
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    raise RuntimeError("Pillow is required for canvas video rendering.") from exc

try:
    from gradium.client import GradiumClient
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    GradiumClient = None  # type: ignore[assignment]

from src.config import get_settings
from src.services.canvas_video_renderer import render_scene_data_to_mp4
from src.services.page_workflow_service import generate_page_workflow
from src.services.text_tokens import normalize_token, tokenize_words

PRIMITIVE_CYCLE = (
    "page_zoom_pan",
    "text_highlight",
    "figure_callout",
    "equation_steps",
    "split_explain",
)


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
        raise RuntimeError("gradium package is not installed in this Python environment.")
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
            sentences = [
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", section["narration_text"])
                if sentence.strip()
            ] or [section["narration_text"]]
            for sentence_index, sentence in enumerate(sentences, start=1):
                client_req_id = f"{section['section_key']}::s{sentence_index:02d}"
                await tts.send_text(sentence, client_req_id=client_req_id)
        await tts.send_eos()

        async for message in tts:
            msg_type = message.get("type")
            if msg_type == "audio":
                audio_chunks.append(message["audio"])
            elif msg_type == "text":
                client_req_id = str(message.get("client_req_id") or "")
                section_key = client_req_id.split("::", 1)[0]
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


def _mock_sections_segments(
    sections: list[dict[str, Any]],
) -> tuple[float, dict[str, list[dict[str, Any]]]]:
    current = 0.0
    segments_by_section: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        section_segments: list[dict[str, Any]] = []
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", section["narration_text"])
            if sentence.strip()
        ] or [section["narration_text"]]
        for sentence in sentences:
            duration = max(0.8, len(tokenize_words(sentence)) / 2.8)
            start_s = current
            stop_s = current + duration
            section_segments.append(
                {
                    "text": sentence,
                    "start_s": start_s,
                    "stop_s": stop_s,
                }
            )
            current = stop_s
        current += 0.2
        segments_by_section[section["section_key"]] = section_segments
    return current, segments_by_section


def _write_silent_wav(path: Path, duration_s: float, sample_rate: int = 24000) -> None:
    frame_count = max(1, int(duration_s * sample_rate))
    silence = b"\x00\x00" * frame_count
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence)


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
            raise RuntimeError(f"{path}: {section['section_id']} has no narration_text.")
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
            raise RuntimeError(f"{path}: {section['section_id']} has misaligned lists: {counts}")

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
                raise RuntimeError(
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
                    "primitive": str(bundle["action"].get("primitive", "text_highlight")),
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


def render_page_video(
    *,
    pdf_path: Path,
    page: int,
    output_dir: Path,
    max_sections: int,
    max_highlights: int,
    max_candidates: int,
    fps: int,
    voice_id: str | None = None,
    use_mock_voice: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow_dir = output_dir / "workflow"
    render_dir = output_dir / "render"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)

    workflow_result = generate_page_workflow(
        pdf_path=pdf_path,
        page=page,
        max_sections=max_sections,
        max_highlights=max_highlights,
        max_candidates=max_candidates,
        output_dir=workflow_dir,
    )
    workflow_path = Path(workflow_result["output_json"])
    sections = _load_sections_from_workflow(workflow_path)
    if not sections:
        raise RuntimeError("No accepted sections found in workflow JSON inputs.")

    sections.sort(key=lambda item: (item["page"], item["section_id"]))
    use_mock = use_mock_voice or ("GRADIUM_API_KEY" not in __import__("os").environ)
    if use_mock and not settings.allow_mock_services:
        raise RuntimeError("GRADIUM_API_KEY is required for video generation.")

    if use_mock:
        duration_s, segments_by_section = _mock_sections_segments(sections)
        final_audio_path = render_dir / "workflow_canvas_video_audio.wav"
        _write_silent_wav(final_audio_path, duration_s)
    else:
        audio_bytes, segments_by_section = asyncio.run(
            _synthesize_sections_with_gradium(
                sections=sections,
                base_url=__import__("os").getenv("GRADIUM_BASE_URL", settings.gradium_base_url),
                voice_id=voice_id or __import__("os").getenv("GRADIUM_VOICE_ID", settings.gradium_voice_id),
            )
        )
        if not audio_bytes:
            raise RuntimeError("Gradium returned empty audio bytes for workflow render.")
        final_audio_path = render_dir / "workflow_canvas_video_audio.wav"
        final_audio_path.write_bytes(audio_bytes)

    manifest_sections: list[dict[str, Any]] = []
    all_timed_actions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for section in sections:
        unresolved.extend(section.get("workflow_unresolved", []))

    for section_index, section in enumerate(sections, start=1):
        section_segments = segments_by_section.get(section["section_key"], [])
        if not section_segments:
            raise RuntimeError(f"No timing segments returned for {section['section_key']}.")
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
        raise RuntimeError("No timed actions resolved from workflow JSON inputs.")

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
    scene_data_path = render_dir / "workflow_canvas_video_scene_data.json"
    scene_data_path.write_text(json.dumps(scene_data, indent=2, ensure_ascii=True), encoding="utf-8")

    final_video_path = render_dir / "workflow_canvas_video_final.mp4"
    render_scene_data_to_mp4(scene_data=scene_data, output_path=final_video_path, fps=fps)

    result_summary_path = render_dir / "workflow_canvas_video_summary.json"
    summary = {
        "workflow_json_path": str(workflow_path.resolve()),
        "scene_data_path": str(scene_data_path.resolve()),
        "final_audio_path": str(final_audio_path.resolve()),
        "final_video_path": str(final_video_path.resolve()),
        "overlay_image_path": workflow_result.get("overlay_image_path"),
        "page": page,
        "fps": fps,
        "duration_s": duration_s,
        "section_count": len(manifest_sections),
        "timed_action_count": len(all_timed_actions),
        "unresolved": unresolved,
        "voice_mode": "mock" if use_mock else "gradium",
    }
    result_summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    return summary

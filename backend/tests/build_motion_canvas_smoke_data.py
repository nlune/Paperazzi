from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _workflow_candidates() -> list[Path]:
    return sorted(ROOT.glob("test_outputs/gemini_workflow*/gemini_workflow_page-*.json"))


def _accepted_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sections = []
    for section in payload.get("processed_sections", []):
        decision = section.get("decision", {})
        if not decision.get("use_section", False):
            continue
        if not section.get("highlight_bboxes"):
            continue
        sections.append(section)
    return sections


def _union_bbox_norm(entries: list[dict[str, Any]]) -> dict[str, float]:
    x0 = min(float(item["bbox_norm"]["x"]) for item in entries)
    y0 = min(float(item["bbox_norm"]["y"]) for item in entries)
    x1 = max(float(item["bbox_norm"]["x"]) + float(item["bbox_norm"]["w"]) for item in entries)
    y1 = max(float(item["bbox_norm"]["y"]) + float(item["bbox_norm"]["h"]) for item in entries)
    pad_x = 0.04
    pad_y = 0.06
    x0 = max(0.0, x0 - pad_x)
    y0 = max(0.0, y0 - pad_y)
    x1 = min(1.0, x1 + pad_x)
    y1 = min(1.0, y1 + pad_y)
    return {"x": x0, "y": y0, "w": max(0.01, x1 - x0), "h": max(0.01, y1 - y0)}


def _build_section(section: dict[str, Any], order: int, start_s: float) -> tuple[dict[str, Any], float]:
    highlight_bboxes = list(section.get("highlight_bboxes", []))[:2]
    actions = list(section.get("actions", []))
    duration_s = 2.2
    action_span = 0.7
    gap_s = 0.18
    timed_actions: list[dict[str, Any]] = []

    for index, bbox_entry in enumerate(highlight_bboxes):
        action = actions[index] if index < len(actions) else {}
        action_start = start_s + 0.55 + index * (action_span + gap_s)
        action_stop = action_start + action_span
        primitive = str(action.get("primitive", "text_highlight"))
        if primitive == "text_highlight" and index == 0:
            primitive = "page_zoom_pan"
        timed_actions.append(
            {
                "action_id": f"motion-canvas-action-{order:02d}-{index + 1:02d}",
                "word": str(bbox_entry.get("word") or bbox_entry.get("pdf_word") or f"word-{index+1}"),
                "occurrence": int(bbox_entry.get("occurrence") or bbox_entry.get("pdf_occurrence") or 1),
                "primitive": primitive,
                "start_s": action_start,
                "stop_s": action_stop,
                "bbox_norm": bbox_entry["bbox_norm"],
                "label": str(action.get("action") or bbox_entry.get("word") or "highlight"),
            }
        )

    section_payload = {
        "section_id": section["section_id"],
        "section_title": section["section_title"],
        "start_s": start_s,
        "stop_s": start_s + duration_s,
        "caption": str(section.get("narration_text", "")).strip(),
        "focus_bbox_norm": _union_bbox_norm(highlight_bboxes),
        "timed_actions": timed_actions,
    }
    return section_payload, start_s + duration_s


def build_motion_canvas_smoke_data() -> Path:
    candidates = _workflow_candidates()
    if not candidates:
        raise SystemExit("No workflow JSON files found under test_outputs/gemini_workflow*.")

    source_path = next((path for path in candidates if "page-0001" in path.name), candidates[0])
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    accepted_sections = _accepted_sections(payload)
    if not accepted_sections:
        raise SystemExit(f"No accepted sections with highlight boxes in {source_path}.")

    selected_sections = accepted_sections[:2]
    start_s = 0.0
    sections: list[dict[str, Any]] = []
    for order, section in enumerate(selected_sections, start=1):
        built, start_s = _build_section(section, order=order, start_s=start_s)
        sections.append(built)

    output_payload = {
        "page": int(payload["page"]),
        "page_image_path": str(Path(payload["page_image_path"]).resolve()),
        "duration_s": start_s,
        "frame_size": {"width": 1280, "height": 720},
        "sections": sections,
        "source_workflow_json": str(source_path.resolve()),
    }
    output_path = ROOT / "test_outputs" / "motion_canvas_smoke_data.json"
    output_path.write_text(
        json.dumps(output_payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    output_path = build_motion_canvas_smoke_data()
    print(f"Motion Canvas smoke data: {output_path}")


if __name__ == "__main__":
    main()

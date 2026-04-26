from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from google import genai

from src.config import get_settings
from src.models import Primitive, SectionRecord


ALLOWED_PRIMITIVES: tuple[Primitive, ...] = (
    "text_highlight",
    "page_zoom_pan",
    "figure_callout",
    "equation_steps",
    "split_explain",
)


class PlannerError(RuntimeError):
    """Raised when section planning fails."""


@dataclass
class PlannerSectionDraft:
    use_section: bool
    decision_reason: str
    section_summary: str
    narration_text: str
    summary_caption: str
    targets: list[dict]
    actions: list[dict]
    split_required: bool = False
    split_reason: str | None = None
    warning: str | None = None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _sentenceish(text: str, limit: int = 220) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _extract_json_payload(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise PlannerError("Gemini did not return valid JSON.")


def _choose_mock_targets(section: SectionRecord, max_targets: int) -> tuple[list[dict], list[dict]]:
    targets: list[dict] = []
    actions: list[dict] = []
    items = [item for item in section.section_items if item.text.strip()]
    if not items:
        items = [section.section_items[0]] if section.section_items else []

    rank = 0
    for item in items:
        if rank >= max_targets:
            break
        text = _sentenceish(item.text, limit=180)
        if not text:
            continue

        kind = "text"
        primitive: Primitive = "text_highlight"
        if "formula" in item.kind:
            kind = "equation"
            primitive = "equation_steps"
        elif "picture" in item.kind or "chart" in item.kind:
            kind = "figure"
            primitive = "figure_callout"
        elif "table" in item.kind:
            kind = "table"
            primitive = "split_explain"
        elif rank == 0:
            primitive = "page_zoom_pan"

        quote = text.split(".")[0].strip() or text
        spoken_text = quote if quote.endswith(".") else f"{quote}."
        spoken_anchor = _clean_text(spoken_text.rstrip("."))
        targets.append(
            {
                "target_id": f"{section.section_id}-target-{rank + 1}",
                "kind": kind,
                "item_id": item.item_id,
                "page_hint": item.page_hint,
                "source_quote": quote,
                "selection_reason": f"Representative {kind} anchor from the section.",
            }
        )
        actions.append(
            {
                "action_id": f"{section.section_id}-action-{rank + 1}",
                "primitive": primitive,
                "target_id": f"{section.section_id}-target-{rank + 1}",
                "spoken_text": spoken_text,
                "spoken_anchor": spoken_anchor,
                "effect_profile": {
                    "preset": primitive,
                    "overlay_style": "amber_outline",
                },
                "payload": {},
            }
        )
        rank += 1

    if not targets:
        quote = _sentenceish(section.section_text or section.title, limit=180)
        targets.append(
            {
                "target_id": f"{section.section_id}-target-1",
                "kind": "section",
                "item_id": section.section_id,
                "page_hint": section.page_start,
                "source_quote": quote,
                "selection_reason": "Fallback to the section as a whole.",
            }
        )
        actions.append(
            {
                "action_id": f"{section.section_id}-action-1",
                "primitive": "page_zoom_pan",
                "target_id": f"{section.section_id}-target-1",
                "spoken_text": f"{quote}.",
                "spoken_anchor": quote,
                "effect_profile": {
                    "preset": "page_zoom_pan",
                    "overlay_style": "section_frame",
                },
                "payload": {},
            }
        )

    return targets, actions


class MockSectionPlanner:
    def plan(self, *, section: SectionRecord, max_targets: int) -> PlannerSectionDraft:
        if section.char_count < 80:
            return PlannerSectionDraft(
                use_section=False,
                decision_reason="Mock decision gateway skipped a very short or low-content section.",
                section_summary=_sentenceish(section.text_excerpt, limit=140),
                narration_text="",
                summary_caption=section.title,
                targets=[],
                actions=[],
                warning="Using mock planner because Gemini is not configured or was bypassed.",
            )

        targets, actions = _choose_mock_targets(section, max_targets=max_targets)
        narration_text = " ".join(action["spoken_text"] for action in actions).strip()
        summary_caption = section.title
        return PlannerSectionDraft(
            use_section=True,
            decision_reason="Mock decision gateway accepted the section as explainable content.",
            section_summary=_sentenceish(section.text_excerpt, limit=140),
            narration_text=narration_text,
            summary_caption=summary_caption,
            targets=targets,
            actions=actions,
            warning="Using mock planner because Gemini is not configured or was bypassed.",
        )


class GeminiSectionPlanner:
    def __init__(self) -> None:
        self._client = genai.Client()

    def _build_prompt(self, *, section: SectionRecord, max_targets: int) -> str:
        payload = {
            "section_id": section.section_id,
            "section_header": section.title,
            "heading_path": section.heading_path,
            "page_span": [section.page_start, section.page_end],
            "section_role": section.section_role,
            "section_text": section.section_text,
            "section_items": [item.model_dump() for item in section.section_items],
            "constraints": {
                "allowed_primitives": list(ALLOWED_PRIMITIVES),
                "max_targets": max_targets,
                "must_quote_source_text_exactly": True,
                "must_return_valid_json": True,
            },
        }
        return f"""
You are planning educational PDF animations for Revideo.

Return exactly one JSON object and no markdown.

You must:
- first decide if this Docling section should be used in the video
- pick up to {max_targets} visual targets for this section
- quote each target's source text exactly as it appears in the provided section items
- avoid any coordinates or bbox values
- choose only from these primitives: {", ".join(ALLOWED_PRIMITIVES)}
- make each action usable for later voice timing

Required JSON shape:
{{
  "use_section": true,
  "decision_reason": "string",
  "split_required": false,
  "split_reason": null,
  "section_summary": "string",
  "narration_text": "string",
  "summary_caption": "string",
  "targets": [
    {{
      "target_id": "string",
      "kind": "text|figure|equation|table|section",
      "item_id": "string",
      "page_hint": 1,
      "source_quote": "exact source quote",
      "selection_reason": "string"
    }}
  ],
  "actions": [
    {{
      "action_id": "string",
      "primitive": "one allowed primitive",
      "target_id": "string",
      "spoken_text": "short beat sentence used for TTS",
      "spoken_anchor": "exact substring of spoken_text",
      "effect_profile": {{
        "preset": "string",
        "overlay_style": "string"
      }},
      "payload": {{}}
    }}
  ]
}}

Validation rules:
- if use_section is false, targets and actions must be empty arrays and narration_text may be empty
- reject references, acknowledgements, boilerplate, malformed fragments, isolated captions without explainable context, and sections with too little semantic content
- every target_id must be unique
- every action target_id must exist in targets
- spoken_anchor must be an exact substring of spoken_text
- source_quote must come from section_text or section_items text verbatim
- keep spoken_text concise and sentence-sized

Section packet:
{json.dumps(payload, ensure_ascii=True, indent=2)}
""".strip()

    def plan(self, *, section: SectionRecord, max_targets: int) -> PlannerSectionDraft:
        response = self._client.models.generate_content(
            model=get_settings().gemini_model,
            contents=[self._build_prompt(section=section, max_targets=max_targets)],
            config={"response_mime_type": "application/json"},
        )
        if not response.text or not response.text.strip():
            raise PlannerError("Gemini returned an empty section plan.")
        payload = _extract_json_payload(response.text)
        return PlannerSectionDraft(
            use_section=bool(payload.get("use_section", True)),
            decision_reason=_clean_text(payload.get("decision_reason", "")),
            split_required=bool(payload.get("split_required", False)),
            split_reason=(
                _clean_text(str(payload.get("split_reason")))
                if payload.get("split_reason") is not None
                else None
            ),
            section_summary=_clean_text(payload.get("section_summary", "")),
            narration_text=_clean_text(payload.get("narration_text", "")),
            summary_caption=_clean_text(payload.get("summary_caption", "")),
            targets=list(payload.get("targets", [])),
            actions=list(payload.get("actions", [])),
        )


def get_section_planner(force_mock: bool = False):
    has_api_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    if force_mock or not has_api_key:
        return MockSectionPlanner()
    return GeminiSectionPlanner()

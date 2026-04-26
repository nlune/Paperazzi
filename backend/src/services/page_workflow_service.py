from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from google import genai

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:  # pragma: no cover - optional visual artifact
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

from src.config import get_settings
from src.models import PageBBox
from src.services.docling_service import (
    build_item_page_bbox_index,
    build_sections,
    convert_pdf_with_docling,
)
from src.services.pdf_service import (
    build_word_index,
    page_bbox_to_image_rect,
    render_pages,
    section_word_refs,
    slice_page_bbox_horizontal,
    union_page_bboxes,
)
from src.services.planner import ALLOWED_PRIMITIVES
from src.services.text_tokens import normalize_token, occurrence_numbers, tokenize_words

STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "between",
    "but",
    "can",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "may",
    "more",
    "not",
    "our",
    "that",
    "the",
    "their",
    "these",
    "this",
    "through",
    "was",
    "were",
    "which",
    "with",
}

PRESENTATION_SKIP_PATTERNS = (
    "grants permission to reproduce",
    "proper attribution",
    "journalistic or scholarly use",
    "solely for",
    "usage policy",
    "copyright",
    "all rights reserved",
    "license",
    "permission to reproduce",
)


def _extract_json_payload(text: str) -> dict[str, Any]:
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
        raise


def _compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rsplit(" ", 1)[0].rstrip() + "..."


def _page_sections(sections: list[Any], page: int) -> list[Any]:
    return [
        section
        for section in sections
        if section.included and any(page_bbox.page == page for page_bbox in section.page_bboxes)
    ]


def _presentation_prefilter_reason(section: Any) -> str | None:
    title = re.sub(r"\s+", " ", section.title).strip().casefold()
    text = re.sub(r"\s+", " ", section.section_text).strip().casefold()
    combined = f"{title}\n{text}"

    if any(pattern in combined for pattern in PRESENTATION_SKIP_PATTERNS):
        return "Auto-skipped legal, permission, or policy boilerplate that is not suitable for an engaging teaching presentation."
    if title in {"title", "authors", "references", "acknowledgements"}:
        return "Auto-skipped front matter or back matter that is not presentation content."
    return None


def _candidate_rows(
    section: Any,
    word_index: list[Any],
    page: int,
    max_candidates: int,
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], Any], dict[str, list[Any]]]:
    refs = [ref for ref in section_word_refs(section, word_index) if ref.page == page]
    occurrences_by_word: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    lookup: dict[tuple[str, int], Any] = {}
    grouped: dict[str, list[Any]] = {}

    for index, ref in enumerate(refs):
        normalized = normalize_token(ref.text)
        if len(normalized) < 3 or normalized.isdigit() or normalized in STOPWORDS:
            continue
        occurrences_by_word[normalized] = occurrences_by_word.get(normalized, 0) + 1
        occurrence = occurrences_by_word[normalized]
        before = " ".join(item.text for item in refs[max(0, index - 5) : index])
        after = " ".join(item.text for item in refs[index + 1 : index + 6])
        row = {
            "word": normalized,
            "pdf_occurrence": occurrence,
            "left_context": _compact_text(before, 90),
            "right_context": _compact_text(after, 90),
        }
        rows.append(row)
        lookup[(normalized, occurrence)] = {
            "ref": ref,
            "word": normalized,
            "pdf_occurrence": occurrence,
            "left_context": row["left_context"],
            "right_context": row["right_context"],
        }
        grouped.setdefault(normalized, []).append(lookup[(normalized, occurrence)])
        if len(rows) >= max_candidates:
            break

    return rows, lookup, grouped


def _call_gemini(
    *,
    client: genai.Client,
    model_name: str,
    page: int,
    section: Any,
    candidate_rows: list[dict[str, Any]],
    max_highlights: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    section_packet = {
        "section_id": section.section_id,
        "section_header": section.title,
        "heading_path": section.heading_path,
        "page_span": [section.page_start, section.page_end],
        "section_role": section.section_role,
        "section_text": _compact_text(section.section_text, 5000),
        "section_items": [
            {
                "item_id": item.item_id,
                "kind": item.kind,
                "page_hint": item.page_hint,
                "text": _compact_text(item.text, 1000),
            }
            for item in section.section_items
            if item.page_hint in {None, page}
        ],
        "highlight_candidates": candidate_rows,
        "constraints": {
            "allowed_primitives": list(ALLOWED_PRIMITIVES),
            "max_highlights": max_highlights,
            "must_select_word_and_pdf_occurrence_from_candidates": True,
            "must_return_json_only": True,
            "no_bbox_values_are_provided": True,
        },
    }
    prompt = f"""
You are testing a PDF-to-Revideo analysis workflow.

Return exactly one JSON object and no markdown.

Task:
- Decide whether this Docling section should be processed for an engaging teaching presentation.
- If yes, write concise narration text for this section.
- Select up to {max_highlights} exact highlight word instances using only pdf_word and pdf_occurrence from the candidate table.
- Pick the animation primitive/action for each highlight.
- For each action, identify the narration word and occurrence in narration_text that should trigger that highlight.

Important duplicate rule:
- The same word can appear multiple times in the PDF and in narration_text.
- Use pdf_word plus pdf_occurrence to identify the exact PDF word instance.
- Use narration_word plus narration_occurrence to identify the exact narration word instance.
- If the narration uses a different word than the PDF source word, still provide the exact narration word and occurrence.
- If section_items include a picture item and the section is explaining a diagram or figure, prefer figure-relevant words with primitive figure_callout.
- Be selective. Skip legal notices, usage policies, permissions, copyright text, publication metadata, and other non-teaching boilerplate.

Required JSON shape:
{{
  "use_section": true,
  "decision_reason": "string",
  "narration_text": "string",
  "highlight_instances": [
    {{
      "pdf_word": "exact candidate word",
      "pdf_occurrence": 1,
      "selection_reason": "string"
    }}
  ],
  "actions": [
    {{
      "pdf_word": "exact candidate word",
      "pdf_occurrence": 1,
      "primitive": "one allowed primitive",
      "action": "short action description",
      "narration_word": "word from narration_text",
      "narration_occurrence": 1,
      "effect_profile": {{"preset": "string", "overlay_style": "string"}}
    }}
  ]
}}

Validation:
- If use_section is false, narration_text may be empty and both arrays must be empty.
- Every pdf_word and pdf_occurrence pair must exist in highlight_candidates.
- actions must have the same pdf_word and pdf_occurrence order as highlight_instances.
- primitive must be one of: {", ".join(ALLOWED_PRIMITIVES)}
- narration_word must appear in narration_text at least narration_occurrence times.

SECTION_PACKET:
{json.dumps(section_packet, ensure_ascii=True, indent=2)}
""".strip()

    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
        config={"response_mime_type": "application/json"},
    )
    if not response.text or not response.text.strip():
        raise RuntimeError(f"Gemini returned empty output for {section.section_id}.")
    payload = _extract_json_payload(response.text)
    return payload, section_packet


def _narration_table(narration_text: str) -> list[dict[str, Any]]:
    tokens = tokenize_words(narration_text)
    occurrences = occurrence_numbers(tokens)
    return [
        {
            "narration_word_id": f"nword-{index:04d}",
            "order": index,
            "word": token,
            "normalized_word": normalize_token(token),
            "occurrence": occurrence,
        }
        for index, (token, occurrence) in enumerate(
            zip(tokens, occurrences, strict=False),
            start=1,
        )
    ]


def _find_narration_word(
    *,
    narration_words: list[dict[str, Any]],
    requested_word: str,
    requested_occurrence: int,
    fallback_word: str,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = normalize_token(requested_word)
    same_word = [word for word in narration_words if word["normalized_word"] == normalized]
    if same_word:
        index = (max(1, requested_occurrence) - 1) % len(same_word)
        chosen = same_word[index]
        warning = None
        if chosen["occurrence"] != requested_occurrence:
            warning = (
                f"Requested narration word {requested_word!r} occurrence "
                f"{requested_occurrence} was out of bounds; used occurrence "
                f"{chosen['occurrence']} via modulo wrap."
            )
        return chosen, warning

    fallback = normalize_token(fallback_word)
    fallback_words = [word for word in narration_words if word["normalized_word"] == fallback]
    if fallback_words:
        return fallback_words[0], (
            f"Requested narration word {requested_word!r} was missing; used "
            f"first narration occurrence of fallback word {fallback!r}."
        )

    return None, (
        f"Could not map highlight word {fallback_word!r} to any narration word. "
        f"Requested {requested_word!r} occurrence {requested_occurrence}."
    )


def _resolve_pdf_candidate(
    *,
    pdf_word: str,
    pdf_occurrence: int,
    candidate_lookup: dict[tuple[str, int], Any],
    candidates_by_word: dict[str, list[Any]],
) -> tuple[Any | None, str | None]:
    normalized = normalize_token(pdf_word)
    direct = candidate_lookup.get((normalized, pdf_occurrence))
    if direct is not None:
        return direct, None

    same_word = candidates_by_word.get(normalized, [])
    if same_word:
        index = (max(1, pdf_occurrence) - 1) % len(same_word)
        chosen = same_word[index]
        return chosen, (
            f"Requested pdf word {pdf_word!r} occurrence {pdf_occurrence} was "
            f"out of bounds; used occurrence {chosen['pdf_occurrence']} via modulo wrap."
        )

    return None, f"Gemini selected unknown pdf word {pdf_word!r}."


def _figure_focus_bbox(
    *,
    page: int,
    section: Any,
    action: dict[str, Any],
    docling_item_bboxes: dict[str, list[PageBBox]],
) -> tuple[PageBBox | None, str | None]:
    picture_bboxes: list[PageBBox] = []
    for item in section.section_items:
        if item.page_hint not in {None, page}:
            continue
        if "picture" not in str(item.kind).casefold():
            continue
        picture_bboxes.extend(
            bbox for bbox in docling_item_bboxes.get(item.item_id, []) if bbox.page == page
        )

    if not picture_bboxes:
        return None, "No Docling picture bbox found for figure_callout; used word bbox."

    focus_bbox = union_page_bboxes(picture_bboxes)
    hint_text = " ".join(
        [
            str(action.get("action", "")),
            str(action.get("pdf_word", "")),
            str(action.get("narration_word", "")),
        ]
    ).casefold()
    if "encoder" in hint_text or "left" in hint_text:
        return slice_page_bbox_horizontal(focus_bbox, side="left"), None
    if "decoder" in hint_text or "right" in hint_text:
        return slice_page_bbox_horizontal(focus_bbox, side="right"), None
    return focus_bbox, None


def _validated_section_output(
    *,
    page: int,
    section: Any,
    gemini_output: dict[str, Any],
    candidate_lookup: dict[tuple[str, int], Any],
    candidates_by_word: dict[str, list[Any]],
    docling_item_bboxes: dict[str, list[PageBBox]],
    page_image_width: int,
    page_image_height: int,
) -> dict[str, Any]:
    unresolved: list[str] = []
    narration_text = str(gemini_output.get("narration_text", "")).strip()
    narration_words = _narration_table(narration_text)

    raw_highlights = list(gemini_output.get("highlight_instances", []))
    raw_actions = list(gemini_output.get("actions", []))
    actions_by_word_occurrence = {
        (
            normalize_token(str(action.get("pdf_word", ""))),
            int(action.get("pdf_occurrence", 1) or 1),
        ): action
        for action in raw_actions
        if action.get("pdf_word")
    }

    highlight_words: list[dict[str, Any]] = []
    highlight_bboxes: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    narration_links: list[dict[str, Any]] = []
    seen_word_occurrences: set[tuple[str, int]] = set()

    for order, item in enumerate(raw_highlights, start=1):
        pdf_word = normalize_token(str(item.get("pdf_word", "")).strip())
        pdf_occurrence = int(item.get("pdf_occurrence", 1) or 1)
        word_occurrence = (pdf_word, pdf_occurrence)
        if not pdf_word or word_occurrence in seen_word_occurrences:
            continue
        candidate, candidate_warning = _resolve_pdf_candidate(
            pdf_word=pdf_word,
            pdf_occurrence=pdf_occurrence,
            candidate_lookup=candidate_lookup,
            candidates_by_word=candidates_by_word,
        )
        if candidate is None:
            unresolved.append(
                f"Gemini selected unresolved pdf word occurrence {pdf_word}#{pdf_occurrence}."
            )
            continue
        if candidate_warning:
            unresolved.append(candidate_warning)
        seen_word_occurrences.add(word_occurrence)
        ref = candidate["ref"]
        action = actions_by_word_occurrence.get(word_occurrence, {})
        primitive = str(action.get("primitive", "text_highlight"))
        if primitive not in ALLOWED_PRIMITIVES:
            unresolved.append(
                f"Invalid primitive {primitive!r} for {pdf_word}#{pdf_occurrence}; using text_highlight."
            )
            primitive = "text_highlight"
        bbox = PageBBox(page=ref.page, bbox=ref.bbox, bbox_norm=ref.bbox_norm)
        if primitive == "figure_callout":
            figure_bbox, figure_warning = _figure_focus_bbox(
                page=page,
                section=section,
                action=action,
                docling_item_bboxes=docling_item_bboxes,
            )
            if figure_bbox is not None:
                bbox = figure_bbox
            elif figure_warning:
                unresolved.append(f"{pdf_word}#{pdf_occurrence}: {figure_warning}")
        image_rect = page_bbox_to_image_rect(
            bbox,
            image_width=page_image_width,
            image_height=page_image_height,
        )
        narration_word, link_warning = _find_narration_word(
            narration_words=narration_words,
            requested_word=str(action.get("narration_word", candidate["word"])),
            requested_occurrence=int(action.get("narration_occurrence", 1) or 1),
            fallback_word=candidate["word"],
        )
        if link_warning:
            unresolved.append(f"{pdf_word}#{pdf_occurrence}: {link_warning}")

        highlight_words.append(
            {
                "pdf_word": candidate["word"],
                "pdf_occurrence": candidate["pdf_occurrence"],
                "order": order,
                "word": candidate["word"],
                "occurrence": candidate["pdf_occurrence"],
                "page": ref.page,
                "word_index": ref.word_index,
                "left_context": candidate["left_context"],
                "right_context": candidate["right_context"],
            }
        )
        highlight_bboxes.append(
            {
                "pdf_word": candidate["word"],
                "pdf_occurrence": candidate["pdf_occurrence"],
                "word": candidate["word"],
                "occurrence": candidate["pdf_occurrence"],
                "bbox": bbox.bbox.model_dump(mode="json"),
                "bbox_norm": bbox.bbox_norm.model_dump(mode="json"),
                "image_rect": [round(value, 2) for value in image_rect],
            }
        )
        actions.append(
            {
                "pdf_word": candidate["word"],
                "pdf_occurrence": candidate["pdf_occurrence"],
                "primitive": primitive,
                "action": str(action.get("action", "highlight this word")),
                "effect_profile": action.get(
                    "effect_profile",
                    {"preset": primitive, "overlay_style": "amber_outline"},
                ),
            }
        )
        narration_links.append(
            {
                "pdf_word": candidate["word"],
                "pdf_occurrence": candidate["pdf_occurrence"],
                "highlight_word": candidate["word"],
                "highlight_occurrence": candidate["pdf_occurrence"],
                "narration_word_id": narration_word["narration_word_id"] if narration_word else None,
                "narration_word": narration_word["word"] if narration_word else None,
                "normalized_narration_word": narration_word["normalized_word"] if narration_word else None,
                "narration_occurrence": narration_word["occurrence"] if narration_word else None,
            }
        )

    if not (
        len(highlight_words)
        == len(highlight_bboxes)
        == len(actions)
        == len(narration_links)
    ):
        unresolved.append("Output list sizes diverged after validation.")

    return {
        "section_id": section.section_id,
        "section_title": section.title,
        "page": page,
        "decision": {
            "use_section": bool(gemini_output.get("use_section", False)),
            "reason": str(gemini_output.get("decision_reason", "")),
        },
        "narration_text": narration_text,
        "narration_words": narration_words,
        "highlight_words": highlight_words,
        "highlight_bboxes": highlight_bboxes,
        "actions": actions,
        "narration_highlight_links": narration_links,
        "unresolved": unresolved,
        "raw_gemini_output": gemini_output,
    }


def _draw_overlay(
    *,
    image_path: Path,
    output_path: Path,
    sections: list[dict[str, Any]],
) -> None:
    if Image is None or ImageDraw is None:
        return
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("Arial.ttf", 18) if ImageFont else None
    except OSError:
        font = None

    colors = ["#e11d48", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0891b2"]
    color_index = 0
    for section in sections:
        for bbox in section.get("highlight_bboxes", []):
            color = colors[color_index % len(colors)]
            x0, y0, x1, y1 = bbox["image_rect"]
            draw.rectangle((x0, y0, x1, y1), outline=color, width=4)
            label = f"{section['section_id']}:{bbox['pdf_word']}#{bbox['pdf_occurrence']}"
            if font is not None:
                draw.text((x0 + 4, max(0, y0 - 22)), label, fill=color, font=font)
            color_index += 1
    image.save(output_path)


def generate_page_workflow(
    *,
    pdf_path: Path,
    page: int,
    max_sections: int,
    max_highlights: int,
    max_candidates: int,
    output_dir: Path,
    model_name: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.exists():
        raise RuntimeError(f"Missing PDF: {pdf_path}")
    resolved_api_key = api_key or __import__("os").getenv("GOOGLE_API_KEY") or __import__("os").getenv("GEMINI_API_KEY")
    if not resolved_api_key:
        raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY is required for page workflow generation.")

    settings = get_settings()
    resolved_model = model_name or __import__("os").getenv("GEMINI_MODEL", settings.gemini_model)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pages = render_pages(pdf_path, output_dir / "pages", settings.default_page_image_dpi)
    page_meta = next((page_meta for page_meta in pages if page_meta.page == page), None)
    if page_meta is None:
        raise RuntimeError(f"Selected page {page} is unavailable.")

    if Image is not None:
        image = Image.open(page_meta.image_path)
        image_width, image_height = image.size
        image.close()
    else:
        image_width = int(page_meta.width * settings.default_page_image_dpi / 72.0)
        image_height = int(page_meta.height * settings.default_page_image_dpi / 72.0)

    word_index = build_word_index(pdf_path)
    document = convert_pdf_with_docling(pdf_path)
    sections = build_sections(document)
    docling_item_bboxes = build_item_page_bbox_index(document)
    candidate_sections = _page_sections(sections, page)
    if not candidate_sections:
        raise RuntimeError(f"No Docling sections found on page {page}.")

    client = genai.Client(api_key=resolved_api_key)
    processed_sections: list[dict[str, Any]] = []
    candidate_section_summaries: list[dict[str, Any]] = []
    gemini_inputs: dict[str, Any] = {}

    for section in candidate_sections:
        prefilter_reason = _presentation_prefilter_reason(section)
        if prefilter_reason is not None:
            processed_sections.append(
                {
                    "section_id": section.section_id,
                    "section_title": section.title,
                    "page": page,
                    "decision": {"use_section": False, "reason": prefilter_reason},
                    "narration_text": "",
                    "narration_words": [],
                    "highlight_words": [],
                    "highlight_bboxes": [],
                    "actions": [],
                    "narration_highlight_links": [],
                    "unresolved": [],
                    "raw_gemini_output": {"use_section": False, "decision_reason": prefilter_reason},
                }
            )
            candidate_section_summaries.append(
                {
                    "section_id": section.section_id,
                    "title": section.title,
                    "char_count": section.char_count,
                    "candidate_count": 0,
                    "prefiltered": True,
                }
            )
            continue

        rows, lookup, grouped = _candidate_rows(section, word_index, page, max_candidates=max_candidates)
        candidate_section_summaries.append(
            {
                "section_id": section.section_id,
                "title": section.title,
                "char_count": section.char_count,
                "candidate_count": len(rows),
                "prefiltered": False,
            }
        )
        if len(rows) < 1:
            continue
        gemini_output, gemini_input = _call_gemini(
            client=client,
            model_name=resolved_model,
            page=page,
            section=section,
            candidate_rows=rows,
            max_highlights=max_highlights,
        )
        gemini_inputs[section.section_id] = gemini_input
        if not bool(gemini_output.get("use_section", False)):
            processed_sections.append(
                {
                    "section_id": section.section_id,
                    "section_title": section.title,
                    "page": page,
                    "decision": {
                        "use_section": False,
                        "reason": str(gemini_output.get("decision_reason", "")),
                    },
                    "narration_text": "",
                    "narration_words": [],
                    "highlight_words": [],
                    "highlight_bboxes": [],
                    "actions": [],
                    "narration_highlight_links": [],
                    "unresolved": [],
                    "raw_gemini_output": gemini_output,
                }
            )
            continue

        section_output = _validated_section_output(
            page=page,
            section=section,
            gemini_output=gemini_output,
            candidate_lookup=lookup,
            candidates_by_word=grouped,
            docling_item_bboxes=docling_item_bboxes,
            page_image_width=image_width,
            page_image_height=image_height,
        )
        processed_sections.append(section_output)
        accepted_count = sum(1 for item in processed_sections if item["decision"]["use_section"])
        if accepted_count >= max_sections:
            break

    overlay_path = output_dir / f"page-{page:04d}-gemini-highlights.png"
    _draw_overlay(
        image_path=Path(page_meta.image_path),
        output_path=overlay_path,
        sections=[section for section in processed_sections if section["decision"]["use_section"]],
    )

    payload = {
        "pdf_path": str(pdf_path),
        "page": page,
        "model": resolved_model,
        "page_image_path": page_meta.image_path,
        "overlay_image_path": str(overlay_path.resolve()) if overlay_path.exists() else None,
        "candidate_sections": candidate_section_summaries,
        "processed_sections": processed_sections,
        "gemini_inputs_no_bboxes": gemini_inputs,
    }
    output_json = output_dir / f"gemini_workflow_page-{page:04d}.json"
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return {
        "output_json": str(output_json.resolve()),
        "overlay_image_path": str(overlay_path.resolve()) if overlay_path.exists() else None,
        "payload": payload,
    }

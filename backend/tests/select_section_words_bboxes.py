from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from google import genai

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.services.docling_service import build_sections, convert_pdf_with_docling
from src.services.pdf_service import build_word_index, render_pages, section_word_refs, truncate_pdf
from src.services.text_tokens import normalize_token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select words and matching bboxes for one section on one page."
    )
    parser.add_argument("pdf_path", help="Path to source PDF.")
    parser.add_argument("--page", type=int, required=True, help="1-based page number.")
    parser.add_argument(
        "--section-index",
        type=int,
        default=1,
        help="1-based section index among sections on selected page.",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=4,
        help="How many words Gemini should select.",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "test_outputs" / "section_word_bbox_selection.json"),
        help="Output JSON path.",
    )
    return parser.parse_args()


def _pick_section_for_page(sections: list[Any], page: int, section_index: int) -> Any:
    matches = [
        section
        for section in sections
        if any(page_bbox.page == page for page_bbox in section.page_bboxes)
    ]
    if not matches:
        raise SystemExit(f"No sections overlap page {page}.")
    if section_index < 1 or section_index > len(matches):
        raise SystemExit(
            f"section-index must be 1..{len(matches)} for page {page}; got {section_index}."
        )
    return matches[section_index - 1]


def _candidate_tokens_from_refs(section_refs: list[Any]) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for ref in section_refs:
        token = normalize_token(ref.text)
        if len(token) < 3 or token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _occurrence_table(section_refs: list[Any]) -> tuple[list[dict[str, Any]], dict[tuple[str, int], Any]]:
    occurrence_by_word: dict[str, int] = {}
    table: list[dict[str, Any]] = []
    lookup: dict[tuple[str, int], Any] = {}
    for ref in section_refs:
        token = normalize_token(ref.text)
        if len(token) < 3 or token.isdigit():
            continue
        occurrence_by_word[token] = occurrence_by_word.get(token, 0) + 1
        occ = occurrence_by_word[token]
        row = {
            "word": token,
            "occurrence": occ,
            "page": ref.page,
            "word_index": ref.word_index,
        }
        table.append(row)
        lookup[(token, occ)] = ref
    return table, lookup


def _select_words_with_gemini(
    *, section_text: str, occurrence_table: list[dict[str, Any]], max_words: int
) -> list[dict[str, Any]]:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GOOGLE_API_KEY or GEMINI_API_KEY is required.")

    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    client = genai.Client(api_key=api_key)
    prompt = f"""
Pick exactly {max_words} word instances to highlight.
Rules:
- Must come from OCCURRENCE_TABLE only.
- Prefer concrete technical nouns.
- Keep lowercase word field.
- Strict JSON only:
{{"selected_instances":[{{"word":"w","occurrence":1}}]}}

SECTION_TEXT:
{section_text}

OCCURRENCE_TABLE:
{json.dumps(occurrence_table, ensure_ascii=True)}
""".strip()

    print(f"[select] calling Gemini model={model_name} ...", flush=True)
    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
        config={"response_mime_type": "application/json"},
    )
    if not response.text or not response.text.strip():
        raise SystemExit("Gemini returned empty word selection.")
    payload = json.loads(response.text)
    selected_raw = payload.get("selected_instances", [])
    selected: list[dict[str, Any]] = []
    allowed = {(row["word"], int(row["occurrence"])) for row in occurrence_table}
    for item in selected_raw:
        word = normalize_token(str(item.get("word", "")))
        occ = int(item.get("occurrence", 0))
        key = (word, occ)
        if key in allowed and key not in {(x["word"], x["occurrence"]) for x in selected}:
            selected.append({"word": word, "occurrence": occ})
        if len(selected) >= max_words:
            break
    if len(selected) < max_words:
        for row in occurrence_table:
            key = (row["word"], int(row["occurrence"]))
            if key in {(x["word"], x["occurrence"]) for x in selected}:
                continue
            selected.append({"word": row["word"], "occurrence": int(row["occurrence"])})
            if len(selected) >= max_words:
                break
    return selected[:max_words]


def main() -> None:
    args = parse_args()
    settings = get_settings()
    pdf_path = Path(args.pdf_path).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[start] pdf={pdf_path}", flush=True)
    print(f"[start] page={args.page} section_index={args.section_index}", flush=True)
    truncated_pdf, cleanup = truncate_pdf(pdf_path, args.page)
    try:
        print("[step] rendering pages ...", flush=True)
        pages = render_pages(
            truncated_pdf, output_path.parent / "pages", settings.default_page_image_dpi
        )
        page_meta = next((page for page in pages if page.page == args.page), None)
        if page_meta is None:
            raise SystemExit(f"Selected page {args.page} is unavailable after truncation.")

        print("[step] building word index ...", flush=True)
        word_index = build_word_index(truncated_pdf)
        print("[step] running Docling section extraction ...", flush=True)
        document = convert_pdf_with_docling(truncated_pdf)
        sections = build_sections(document)
        section = _pick_section_for_page(sections, args.page, args.section_index)

        refs = section_word_refs(section, word_index)
        page_refs = [ref for ref in refs if ref.page == args.page]
        print(f"[info] section_id={section.section_id} title={section.title!r}", flush=True)
        print(f"[info] words_on_page_in_section={len(page_refs)}", flush=True)
        occurrence_table, occurrence_lookup = _occurrence_table(page_refs)
        if len(occurrence_table) < args.max_words:
            raise SystemExit(
                f"Not enough candidate word instances on page {args.page}; got {len(occurrence_table)}."
            )
        selected_instances = _select_words_with_gemini(
            section_text=re.sub(r"\s+", " ", section.section_text).strip(),
            occurrence_table=occurrence_table,
            max_words=args.max_words,
        )
        print(f"[result] selected_instances={selected_instances}", flush=True)

        selected_entries: list[dict[str, Any]] = []
        for item in selected_instances:
            key = (item["word"], int(item["occurrence"]))
            ref = occurrence_lookup.get(key)
            if ref is None:
                raise SystemExit(
                    f"Selected instance missing in occurrence lookup: {item['word']}#{item['occurrence']}"
                )
            selected_entries.append(
                {
                    "word": item["word"],
                    "occurrence": int(item["occurrence"]),
                    "page": ref.page,
                    "word_index": ref.word_index,
                    "bbox_norm": ref.bbox_norm.model_dump(mode="json"),
                }
            )
        if len(selected_entries) != args.max_words:
            raise SystemExit(
                f"Selected entries count mismatch: expected {args.max_words}, got {len(selected_entries)}."
            )

        payload = {
            "pdf_path": str(pdf_path),
            "page": args.page,
            "section_index": args.section_index,
            "section_id": section.section_id,
            "section_title": section.title,
            "section_text": re.sub(r"\s+", " ", section.section_text).strip(),
            "page_image_path": page_meta.image_path,
            "selected_words": [item["word"] for item in selected_instances],
            "selected_instances": selected_instances,
            "selected_word_bboxes": selected_entries,
        }
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        print(f"[done] wrote selection JSON: {output_path}", flush=True)
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    finally:
        if cleanup:
            truncated_pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

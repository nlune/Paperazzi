from __future__ import annotations

import re
from pathlib import Path
from tempfile import NamedTemporaryFile

import fitz
from pypdf import PdfReader, PdfWriter

from src.models import (
    BBox,
    DocumentPage,
    NormalizedBBox,
    PageBBox,
    SectionRecord,
    SectionWordRef,
    VisualTarget,
    WordBox,
    WordIndexPage,
)
from src.services.text_tokens import normalize_token


def get_pdf_page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def truncate_pdf(pdf_path: Path, page_limit: int | None) -> tuple[Path, bool]:
    if page_limit is None:
        return pdf_path, False

    reader = PdfReader(str(pdf_path))
    if len(reader.pages) <= page_limit:
        return pdf_path, False

    writer = PdfWriter()
    for page in reader.pages[:page_limit]:
        writer.add_page(page)

    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        writer.write(tmp_file)
        return Path(tmp_file.name), True


def _normalize_bbox(rect: fitz.Rect, page_width: float, page_height: float) -> NormalizedBBox:
    return NormalizedBBox(
        x=rect.x0 / page_width if page_width else 0.0,
        y=rect.y0 / page_height if page_height else 0.0,
        w=(rect.x1 - rect.x0) / page_width if page_width else 0.0,
        h=(rect.y1 - rect.y0) / page_height if page_height else 0.0,
    )


def rect_to_bbox(rect: fitz.Rect) -> BBox:
    return BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)


def rect_to_page_bbox(page_no: int, rect: fitz.Rect, width: float, height: float) -> PageBBox:
    return PageBBox(
        page=page_no,
        bbox=rect_to_bbox(rect),
        bbox_norm=_normalize_bbox(rect, width, height),
    )


def word_to_page_bbox(page: WordIndexPage, word: WordBox) -> PageBBox:
    rect = fitz.Rect(word.x0, word.y0, word.x1, word.y1)
    return rect_to_page_bbox(page.page, rect, page.width, page.height)


def page_bbox_to_image_rect(
    page_bbox: PageBBox,
    image_width: int,
    image_height: int,
) -> tuple[float, float, float, float]:
    return (
        page_bbox.bbox_norm.x * image_width,
        page_bbox.bbox_norm.y * image_height,
        (page_bbox.bbox_norm.x + page_bbox.bbox_norm.w) * image_width,
        (page_bbox.bbox_norm.y + page_bbox.bbox_norm.h) * image_height,
    )


def union_page_bboxes(page_bboxes: list[PageBBox]) -> PageBBox:
    if not page_bboxes:
        raise ValueError("At least one PageBBox is required.")
    page = page_bboxes[0].page
    x0 = min(item.bbox.x0 for item in page_bboxes)
    y0 = min(item.bbox.y0 for item in page_bboxes)
    x1 = max(item.bbox.x1 for item in page_bboxes)
    y1 = max(item.bbox.y1 for item in page_bboxes)
    nx0 = min(item.bbox_norm.x for item in page_bboxes)
    ny0 = min(item.bbox_norm.y for item in page_bboxes)
    nx1 = max(item.bbox_norm.x + item.bbox_norm.w for item in page_bboxes)
    ny1 = max(item.bbox_norm.y + item.bbox_norm.h for item in page_bboxes)
    return PageBBox(
        page=page,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        bbox_norm=NormalizedBBox(
            x=nx0,
            y=ny0,
            w=max(0.01, nx1 - nx0),
            h=max(0.01, ny1 - ny0),
        ),
    )


def slice_page_bbox_horizontal(page_bbox: PageBBox, *, side: str) -> PageBBox:
    width = page_bbox.bbox.x1 - page_bbox.bbox.x0
    overlap = width * 0.08
    midpoint = page_bbox.bbox.x0 + width / 2.0
    if side == "left":
        x0 = page_bbox.bbox.x0
        x1 = min(page_bbox.bbox.x1, midpoint + overlap)
    else:
        x0 = max(page_bbox.bbox.x0, midpoint - overlap)
        x1 = page_bbox.bbox.x1

    norm_width = page_bbox.bbox_norm.w
    norm_overlap = norm_width * 0.08
    norm_midpoint = page_bbox.bbox_norm.x + norm_width / 2.0
    if side == "left":
        nx0 = page_bbox.bbox_norm.x
        nx1 = min(page_bbox.bbox_norm.x + norm_width, norm_midpoint + norm_overlap)
    else:
        nx0 = max(page_bbox.bbox_norm.x, norm_midpoint - norm_overlap)
        nx1 = page_bbox.bbox_norm.x + norm_width

    return PageBBox(
        page=page_bbox.page,
        bbox=BBox(
            x0=x0,
            y0=page_bbox.bbox.y0,
            x1=x1,
            y1=page_bbox.bbox.y1,
        ),
        bbox_norm=NormalizedBBox(
            x=nx0,
            y=page_bbox.bbox_norm.y,
            w=max(0.01, nx1 - nx0),
            h=page_bbox.bbox_norm.h,
        ),
    )


def render_pages(pdf_path: Path, output_dir: Path, dpi: int) -> list[DocumentPage]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[DocumentPage] = []

    with fitz.open(pdf_path) as doc:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in range(doc.page_count):
            page = doc[page_index]
            image_path = output_dir / f"page-{page_index + 1:04d}.png"
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pix.save(image_path)
            pages.append(
                DocumentPage(
                    page=page_index + 1,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    image_path=str(image_path.resolve()),
                )
            )

    return pages


def build_word_index(pdf_path: Path) -> list[WordIndexPage]:
    pages: list[WordIndexPage] = []
    with fitz.open(pdf_path) as doc:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            words = page.get_text("words", sort=True)
            word_boxes = [
                WordBox(
                    index=index,
                    page=page_index + 1,
                    text=str(word[4]),
                    x0=float(word[0]),
                    y0=float(word[1]),
                    x1=float(word[2]),
                    y1=float(word[3]),
                    bbox_norm=_normalize_bbox(
                        fitz.Rect(
                            float(word[0]),
                            float(word[1]),
                            float(word[2]),
                            float(word[3]),
                        ),
                        float(page.rect.width),
                        float(page.rect.height),
                    ),
                    block_no=int(word[5]),
                    line_no=int(word[6]),
                    word_no=int(word[7]),
                )
                for index, word in enumerate(words)
            ]
            pages.append(
                WordIndexPage(
                    page=page_index + 1,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    words=word_boxes,
                )
            )
    return pages


def _normalize_match_token(token: str) -> str:
    return normalize_token(token)


def _word_center_in_rect(word: WordBox, rect: fitz.Rect) -> bool:
    center_x = (word.x0 + word.x1) / 2.0
    center_y = (word.y0 + word.y1) / 2.0
    return rect.x0 <= center_x <= rect.x1 and rect.y0 <= center_y <= rect.y1


def _find_word_match(
    words: list[WordBox],
    quote: str,
    within_rect: fitz.Rect | None = None,
) -> list[int]:
    target_tokens = [
        token
        for token in (_normalize_match_token(part) for part in quote.split())
        if token
    ]
    if not target_tokens:
        return []

    normalized_words = [_normalize_match_token(word.text) for word in words]
    first_match: list[int] = []
    for start in range(len(normalized_words)):
        end = start + len(target_tokens)
        if normalized_words[start:end] == target_tokens:
            candidate = list(range(start, end))
            if within_rect is None:
                return candidate
            if not first_match:
                first_match = candidate
            if all(_word_center_in_rect(words[index], within_rect) for index in candidate):
                return candidate
    return first_match


def _rects_for_quote(page: fitz.Page, quote: str) -> list[fitz.Rect]:
    cleaned = re.sub(r"\s+", " ", quote).strip()
    if not cleaned:
        return []
    return list(page.search_for(cleaned))


def resolve_visual_target(
    *,
    pdf_path: Path,
    word_index: list[WordIndexPage],
    unit_id: str,
    target_id: str,
    kind: str,
    label: str,
    anchor_text: str,
    docling_ref: str | None,
    page_hint: int | None,
    page_span: tuple[int, int],
    fallback_page_bbox: PageBBox,
) -> tuple[VisualTarget, str | None]:
    search_pages = []
    if page_hint is not None:
        search_pages.append(page_hint)
    for page_no in range(page_span[0], page_span[1] + 1):
        if page_no not in search_pages:
            search_pages.append(page_no)

    with fitz.open(pdf_path) as doc:
        for page_no in search_pages:
            if page_no < 1 or page_no > doc.page_count:
                continue

            page = doc[page_no - 1]
            page_words = word_index[page_no - 1].words
            rects = _rects_for_quote(page, anchor_text)
            if rects:
                union = fitz.Rect(rects[0])
                for rect in rects[1:]:
                    union.include_rect(rect)
                word_refs = _find_word_match(page_words, anchor_text, within_rect=union)
                return (
                    VisualTarget(
                        target_id=target_id,
                        unit_id=unit_id,
                        kind=kind,  # type: ignore[arg-type]
                        label=label,
                        page=page_no,
                        anchor_text=anchor_text,
                        match_strategy="search_for",
                        docling_ref=docling_ref,
                        word_refs=word_refs,
                        fragments=[
                            rect_to_page_bbox(page_no, rect, page.rect.width, page.rect.height)
                            for rect in rects
                        ],
                        union_bbox=rect_to_bbox(union),
                        union_bbox_norm=_normalize_bbox(
                            union, page.rect.width, page.rect.height
                        ),
                        confidence=0.98,
                        fallback_used=False,
                    ),
                    None,
                )

            word_refs = _find_word_match(page_words, anchor_text)
            if word_refs:
                first = page_words[word_refs[0]]
                union = fitz.Rect(first.x0, first.y0, first.x1, first.y1)
                for ref in word_refs[1:]:
                    word = page_words[ref]
                    union.include_rect(fitz.Rect(word.x0, word.y0, word.x1, word.y1))
                return (
                    VisualTarget(
                        target_id=target_id,
                        unit_id=unit_id,
                        kind=kind,  # type: ignore[arg-type]
                        label=label,
                        page=page_no,
                        anchor_text=anchor_text,
                        match_strategy="word_sequence",
                        docling_ref=docling_ref,
                        word_refs=word_refs,
                        fragments=[
                            rect_to_page_bbox(
                                page_no,
                                fitz.Rect(word.x0, word.y0, word.x1, word.y1),
                                page.rect.width,
                                page.rect.height,
                            )
                            for word in (page_words[ref] for ref in word_refs)
                        ],
                        union_bbox=rect_to_bbox(union),
                        union_bbox_norm=_normalize_bbox(
                            union, page.rect.width, page.rect.height
                        ),
                        confidence=0.88,
                        fallback_used=False,
                    ),
                    None,
                )

    return (
        VisualTarget(
            target_id=target_id,
            unit_id=unit_id,
            kind=kind,  # type: ignore[arg-type]
            label=label,
            page=fallback_page_bbox.page,
            anchor_text=anchor_text,
            match_strategy="section_fallback",
            docling_ref=docling_ref,
            word_refs=[],
            fragments=[fallback_page_bbox],
            union_bbox=fallback_page_bbox.bbox,
            union_bbox_norm=fallback_page_bbox.bbox_norm,
            confidence=0.35,
            fallback_used=True,
        ),
        f"Could not resolve target quote {anchor_text!r}; used section bbox fallback.",
    )


def section_word_refs(
    section: SectionRecord,
    word_index: list[WordIndexPage],
) -> list[SectionWordRef]:
    refs: list[SectionWordRef] = []
    section_bboxes_by_page = {
        page_bbox.page: page_bbox for page_bbox in section.page_bboxes
    }
    if not section_bboxes_by_page:
        return refs

    for page in word_index:
        section_bbox = section_bboxes_by_page.get(page.page)
        if section_bbox is None:
            continue
        for word in page.words:
            center_x = (word.x0 + word.x1) / 2.0
            center_y = (word.y0 + word.y1) / 2.0
            if not (
                section_bbox.bbox.x0 <= center_x <= section_bbox.bbox.x1
                and section_bbox.bbox.y0 <= center_y <= section_bbox.bbox.y1
            ):
                continue
            bbox = BBox(x0=word.x0, y0=word.y0, x1=word.x1, y1=word.y1)
            bbox_norm = word.bbox_norm or _normalize_bbox(
                fitz.Rect(word.x0, word.y0, word.x1, word.y1),
                page.width,
                page.height,
            )
            refs.append(
                SectionWordRef(
                    word_ref_id=f"{section.section_id}:p{page.page}:w{word.index}",
                    section_id=section.section_id,
                    page=page.page,
                    word_index=word.index,
                    text=word.text,
                    bbox=bbox,
                    bbox_norm=bbox_norm,
                )
            )

    return refs

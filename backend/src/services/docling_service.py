from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import (
    ContentLayer,
    DocItem,
    DoclingDocument,
    GroupItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)

from src.models import BBox, NormalizedBBox, PageBBox, SectionItem, SectionRecord


SKIP_SECTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "references": ("references", "bibliography", "works cited"),
    "appendix": ("appendix", "supplementary", "supplemental"),
    "acknowledgements": ("acknowledgements", "acknowledgments"),
}


def _normalize_bbox(
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    page_width: float,
    page_height: float,
) -> NormalizedBBox:
    return NormalizedBBox(
        x=x0 / page_width if page_width else 0.0,
        y=y0 / page_height if page_height else 0.0,
        w=(x1 - x0) / page_width if page_width else 0.0,
        h=(y1 - y0) / page_height if page_height else 0.0,
    )


def _page_bbox_from_points(
    page_no: int,
    points: list[tuple[float, float, float, float]],
    page_width: float,
    page_height: float,
) -> PageBBox:
    x0 = min(point[0] for point in points)
    y0 = min(point[1] for point in points)
    x1 = max(point[2] for point in points)
    y1 = max(point[3] for point in points)
    bbox = BBox(x0=x0, y0=y0, x1=x1, y1=y1)
    return PageBBox(
        page=page_no,
        bbox=bbox,
        bbox_norm=_normalize_bbox(
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            page_width=page_width,
            page_height=page_height,
        ),
    )


def _bbox_width(page_bbox: PageBBox) -> float:
    return page_bbox.bbox.x1 - page_bbox.bbox.x0


def _bbox_height(page_bbox: PageBBox) -> float:
    return page_bbox.bbox.y1 - page_bbox.bbox.y0


def _intersects(a: PageBBox, b: PageBBox) -> bool:
    return not (
        a.bbox.x1 <= b.bbox.x0
        or a.bbox.x0 >= b.bbox.x1
        or a.bbox.y1 <= b.bbox.y0
        or a.bbox.y0 >= b.bbox.y1
    )


def _inside(inner: PageBBox, outer: PageBBox, tol: float = 2.0) -> bool:
    return (
        inner.bbox.x0 >= outer.bbox.x0 - tol
        and inner.bbox.y0 >= outer.bbox.y0 - tol
        and inner.bbox.x1 <= outer.bbox.x1 + tol
        and inner.bbox.y1 <= outer.bbox.y1 + tol
    )


def _translate_page_bbox(
    page_bbox: PageBBox,
    *,
    page_width: float,
    page_height: float,
    dx: float,
    dy: float,
) -> PageBBox:
    x0 = page_bbox.bbox.x0 + dx
    y0 = page_bbox.bbox.y0 + dy
    x1 = page_bbox.bbox.x1 + dx
    y1 = page_bbox.bbox.y1 + dy
    bbox = BBox(x0=x0, y0=y0, x1=x1, y1=y1)
    return PageBBox(
        page=page_bbox.page,
        bbox=bbox,
        bbox_norm=_normalize_bbox(
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            page_width=page_width,
            page_height=page_height,
        ),
    )


def _picture_page_bboxes(document: DoclingDocument) -> dict[int, list[PageBBox]]:
    result: dict[int, list[PageBBox]] = defaultdict(list)
    for picture in document.pictures:
        for prov in picture.prov:
            page = document.pages[prov.page_no]
            bbox = prov.bbox.to_top_left_origin(page_height=page.size.height)
            result[prov.page_no].append(
                PageBBox(
                    page=prov.page_no,
                    bbox=BBox(x0=bbox.l, y0=bbox.t, x1=bbox.r, y1=bbox.b),
                    bbox_norm=_normalize_bbox(
                        x0=bbox.l,
                        y0=bbox.t,
                        x1=bbox.r,
                        y1=bbox.b,
                        page_width=page.size.width,
                        page_height=page.size.height,
                    ),
                )
            )
    return result


def prov_to_page_bbox(
    document: DoclingDocument,
    item: DocItem,
    prov,
    *,
    picture_bboxes_by_page: dict[int, list[PageBBox]] | None = None,
) -> tuple[PageBBox, bool]:
    page = document.pages[prov.page_no]
    bbox = prov.bbox.to_top_left_origin(page_height=page.size.height)
    page_bbox = PageBBox(
        page=prov.page_no,
        bbox=BBox(x0=bbox.l, y0=bbox.t, x1=bbox.r, y1=bbox.b),
        bbox_norm=_normalize_bbox(
            x0=bbox.l,
            y0=bbox.t,
            x1=bbox.r,
            y1=bbox.b,
            page_width=page.size.width,
            page_height=page.size.height,
        ),
    )

    pictures = (
        picture_bboxes_by_page.get(prov.page_no, [])
        if picture_bboxes_by_page is not None
        else _picture_page_bboxes(document).get(prov.page_no, [])
    )
    if not pictures or isinstance(item, (PictureItem, TableItem)):
        return page_bbox, False

    for picture_bbox in pictures:
        translated = _translate_page_bbox(
            page_bbox,
            page_width=page.size.width,
            page_height=page.size.height,
            dx=picture_bbox.bbox.x0,
            dy=picture_bbox.bbox.y0,
        )
        if (
            page_bbox.bbox.x0 >= -2.0
            and page_bbox.bbox.y0 >= -2.0
            and page_bbox.bbox.x1 <= _bbox_width(picture_bbox) + 2.0
            and page_bbox.bbox.y1 <= _bbox_height(picture_bbox) + 2.0
            and not _intersects(page_bbox, picture_bbox)
            and _inside(translated, picture_bbox, tol=4.0)
        ):
            return translated, True

    return page_bbox, False


def _item_text(doc: DoclingDocument, item: DocItem) -> str:
    if isinstance(item, TextItem):
        return item.text
    if isinstance(item, TableItem):
        markdown = item.export_to_markdown(doc=doc).strip()
        return markdown[:1500]
    if isinstance(item, PictureItem):
        return item.caption_text(doc).strip()
    return ""


def _item_kind(item: DocItem) -> str:
    label = getattr(item, "label", None)
    if label is None:
        return "unknown"
    return str(label)


def _skip_policy(heading_path: list[str]) -> tuple[bool, str | None, str]:
    title = " / ".join(heading_path).strip().casefold()
    for role, patterns in SKIP_SECTION_PATTERNS.items():
        if any(pattern in title for pattern in patterns):
            return False, role, f"auto-skipped {role} section"
    return True, "body", None


def _truncate_text(text: str, limit: int = 240) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


class WorkingSection:
    def __init__(
        self,
        *,
        section_id: str,
        order: int,
        title: str,
        level: int,
        heading_path: list[str],
        picture_bboxes_by_page: dict[int, list[PageBBox]],
    ):
        self.section_id = section_id
        self.order = order
        self.title = title
        self.level = level
        self.heading_path = list(heading_path)
        self.picture_bboxes_by_page = picture_bboxes_by_page
        self.docling_refs: list[str] = []
        self.items: list[SectionItem] = []
        self.points_by_page: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        self.page_start: int | None = None
        self.page_end: int | None = None
        self.text_parts: list[str] = []

    def append_item(self, doc: DoclingDocument, item: DocItem) -> None:
        text = _item_text(doc, item).strip()
        self.docling_refs.append(item.self_ref)
        self.items.append(
            SectionItem(
                item_id=item.self_ref,
                kind=_item_kind(item),
                page_hint=item.prov[0].page_no if item.prov else None,
                text=text,
            )
        )
        if text:
            self.text_parts.append(text)
        for prov in item.prov:
            page_bbox, _ = prov_to_page_bbox(
                doc,
                item,
                prov,
                picture_bboxes_by_page=self.picture_bboxes_by_page,
            )
            self.points_by_page[prov.page_no].append(
                (
                    page_bbox.bbox.x0,
                    page_bbox.bbox.y0,
                    page_bbox.bbox.x1,
                    page_bbox.bbox.y1,
                )
            )
            self.page_start = prov.page_no if self.page_start is None else min(self.page_start, prov.page_no)
            self.page_end = prov.page_no if self.page_end is None else max(self.page_end, prov.page_no)

    def build(self, doc: DoclingDocument) -> SectionRecord | None:
        section_text = "\n\n".join(part for part in self.text_parts if part).strip()
        if not section_text and not self.points_by_page:
            return None

        page_bboxes = []
        for page_no in sorted(self.points_by_page):
            page = doc.pages[page_no]
            page_bboxes.append(
                _page_bbox_from_points(
                    page_no=page_no,
                    points=self.points_by_page[page_no],
                    page_width=page.size.width,
                    page_height=page.size.height,
                )
            )

        included, role, skip_reason = _skip_policy(self.heading_path or [self.title])
        return SectionRecord(
            section_id=self.section_id,
            order=self.order,
            title=self.title,
            level=self.level,
            heading_path=self.heading_path,
            section_role=role,  # type: ignore[arg-type]
            page_start=self.page_start or 1,
            page_end=self.page_end or self.page_start or 1,
            docling_refs=self.docling_refs,
            page_bboxes=page_bboxes,
            text_excerpt=_truncate_text(section_text or self.title),
            section_text=section_text,
            char_count=len(section_text),
            included=included,
            skip_reason=skip_reason,
            section_items=self.items,
        )


def convert_pdf_with_docling(pdf_path: Path) -> DoclingDocument:
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=PdfPipelineOptions(generate_page_images=False)
            )
        }
    )
    result = converter.convert(pdf_path)
    return result.document


def build_sections(document: DoclingDocument) -> list[SectionRecord]:
    sections: list[SectionRecord] = []
    picture_bboxes_by_page = _picture_page_bboxes(document)
    heading_stack: list[tuple[int, str]] = []
    title_text = document.name
    current: WorkingSection | None = None
    section_index = 0

    def finalize_current() -> None:
        nonlocal current
        if current is None:
            return
        built = current.build(document)
        if built is not None:
            sections.append(built)
        current = None

    for node, _ in document.iterate_items(
        with_groups=False,
        traverse_pictures=True,
        included_content_layers={ContentLayer.BODY},
    ):
        if not isinstance(node, DocItem):
            continue

        if isinstance(node, TitleItem):
            title_text = node.text.strip() or title_text
            if not heading_stack:
                heading_stack = [(0, title_text)]
            continue

        if isinstance(node, SectionHeaderItem):
            finalize_current()
            level = int(node.level)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, node.text.strip()))
            section_index += 1
            current = WorkingSection(
                section_id=f"section-{section_index:03d}",
                order=section_index,
                title=node.text.strip() or f"Section {section_index}",
                level=level,
                heading_path=[title_text] + [part for _, part in heading_stack],
                picture_bboxes_by_page=picture_bboxes_by_page,
            )
            current.append_item(document, node)
            continue

        if current is None:
            section_index += 1
            current = WorkingSection(
                section_id=f"section-{section_index:03d}",
                order=section_index,
                title="Introduction",
                level=1,
                heading_path=[title_text, "Introduction"],
                picture_bboxes_by_page=picture_bboxes_by_page,
            )

        current.append_item(document, node)

    finalize_current()
    return sections


def build_item_page_bbox_index(document: DoclingDocument) -> dict[str, list[PageBBox]]:
    item_bboxes: dict[str, list[PageBBox]] = {}
    picture_bboxes_by_page = _picture_page_bboxes(document)

    for node, _ in document.iterate_items(
        with_groups=False,
        traverse_pictures=True,
        included_content_layers={ContentLayer.BODY},
    ):
        if not isinstance(node, DocItem):
            continue
        page_bboxes: list[PageBBox] = []
        for prov in node.prov:
            page_bbox, _ = prov_to_page_bbox(
                document,
                node,
                prov,
                picture_bboxes_by_page=picture_bboxes_by_page,
            )
            page_bboxes.append(page_bbox)
        if page_bboxes:
            item_bboxes[node.self_ref] = page_bboxes

    return item_bboxes


def build_docling_payload(document: DoclingDocument) -> dict:
    return document.export_to_dict()

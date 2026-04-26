from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw
from docling_core.types.doc.document import ContentLayer, DocItem

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.services.docling_service import (
    convert_pdf_with_docling,
    prov_to_page_bbox,
)
from src.services.pdf_service import (
    build_word_index,
    page_bbox_to_image_rect,
    render_pages,
    truncate_pdf,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render Docling and PyMuPDF bbox overlays onto page PNGs."
    )
    parser.add_argument("pdf_path", help="Path to the source PDF.")
    parser.add_argument(
        "--page-limit",
        type=int,
        default=3,
        help="How many pages to inspect from the start of the PDF.",
    )
    parser.add_argument(
        "--output-dir",
        default="storage/bbox-debug",
        help="Where to save the overlay images.",
    )
    return parser.parse_args()


def _draw_docling_boxes(base_image: Image.Image, document, page_number: int) -> tuple[Image.Image, int]:
    overlay = base_image.copy()
    draw = ImageDraw.Draw(overlay)
    count = 0
    page = document.pages[page_number]

    for item, _ in document.iterate_items(
        with_groups=False,
        traverse_pictures=True,
        included_content_layers={ContentLayer.BODY},
    ):
        if not isinstance(item, DocItem):
            continue
        for prov in item.prov:
            if prov.page_no != page_number:
                continue
            page_bbox, _ = prov_to_page_bbox(document, item, prov)
            x0, y0, x1, y1 = page_bbox_to_image_rect(
                page_bbox, overlay.width, overlay.height
            )
            draw.rectangle((x0, y0, x1, y1), outline=(255, 64, 64), width=2)
            count += 1

    return overlay, count


def _draw_pymupdf_boxes(base_image: Image.Image, word_page) -> tuple[Image.Image, int]:
    overlay = base_image.copy()
    draw = ImageDraw.Draw(overlay)
    count = 0
    for word in word_page.words:
        x0 = (word.x0 / word_page.width) * overlay.width
        y0 = (word.y0 / word_page.height) * overlay.height
        x1 = (word.x1 / word_page.width) * overlay.width
        y1 = (word.y1 / word_page.height) * overlay.height
        draw.rectangle((x0, y0, x1, y1), outline=(64, 200, 255), width=1)
        count += 1
    return overlay, count


def _draw_combined(
    base_image: Image.Image,
    document,
    page_number: int,
    word_page,
) -> Image.Image:
    combined = base_image.copy()
    draw = ImageDraw.Draw(combined)
    page = document.pages[page_number]

    for item, _ in document.iterate_items(
        with_groups=False,
        traverse_pictures=True,
        included_content_layers={ContentLayer.BODY},
    ):
        if not isinstance(item, DocItem):
            continue
        for prov in item.prov:
            if prov.page_no != page_number:
                continue
            page_bbox, _ = prov_to_page_bbox(document, item, prov)
            x0, y0, x1, y1 = page_bbox_to_image_rect(
                page_bbox, combined.width, combined.height
            )
            draw.rectangle((x0, y0, x1, y1), outline=(255, 64, 64), width=2)

    for word in word_page.words:
        x0 = (word.x0 / word_page.width) * combined.width
        y0 = (word.y0 / word_page.height) * combined.height
        x1 = (word.x1 / word_page.width) * combined.width
        y1 = (word.y1 / word_page.height) * combined.height
        draw.rectangle((x0, y0, x1, y1), outline=(64, 200, 255), width=1)

    return combined


def main() -> None:
    args = parse_args()
    source_pdf = Path(args.pdf_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    truncated_pdf, cleanup = truncate_pdf(source_pdf, args.page_limit)
    try:
        pages = render_pages(
            truncated_pdf, output_dir / "pages", get_settings().default_page_image_dpi
        )
        word_index = build_word_index(truncated_pdf)
        document = convert_pdf_with_docling(truncated_pdf)

        summary: dict[str, object] = {
            "source_pdf": str(source_pdf),
            "page_limit": args.page_limit,
            "pages": [],
        }

        for page_meta in pages[: args.page_limit]:
            base_path = Path(page_meta.image_path)
            base_image = Image.open(base_path).convert("RGB")
            word_page = word_index[page_meta.page - 1]

            docling_overlay, docling_count = _draw_docling_boxes(
                base_image, document, page_meta.page
            )
            pymupdf_overlay, pymupdf_count = _draw_pymupdf_boxes(base_image, word_page)
            combined_overlay = _draw_combined(
                base_image, document, page_meta.page, word_page
            )

            docling_path = output_dir / f"page-{page_meta.page:04d}-docling.png"
            pymupdf_path = output_dir / f"page-{page_meta.page:04d}-pymupdf.png"
            combined_path = output_dir / f"page-{page_meta.page:04d}-combined.png"

            docling_overlay.save(docling_path)
            pymupdf_overlay.save(pymupdf_path)
            combined_overlay.save(combined_path)

            summary["pages"].append(
                {
                    "page": page_meta.page,
                    "pdf_size": {"width": page_meta.width, "height": page_meta.height},
                    "image_size": {
                        "width": base_image.width,
                        "height": base_image.height,
                    },
                    "scale": {
                        "x": base_image.width / page_meta.width if page_meta.width else 0,
                        "y": base_image.height / page_meta.height if page_meta.height else 0,
                    },
                    "docling_boxes": docling_count,
                    "pymupdf_boxes": pymupdf_count,
                    "base_image": str(base_path),
                    "docling_overlay": str(docling_path),
                    "pymupdf_overlay": str(pymupdf_path),
                    "combined_overlay": str(combined_path),
                }
            )

        summary_path = output_dir / "bbox_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    finally:
        if cleanup:
            truncated_pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

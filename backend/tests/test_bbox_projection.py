from __future__ import annotations

import unittest

from src.models import BBox, NormalizedBBox, PageBBox
from src.services.pdf_service import (
    page_bbox_to_image_rect,
    slice_page_bbox_horizontal,
    union_page_bboxes,
)


class BboxProjectionTests(unittest.TestCase):
    def test_page_bbox_projects_using_normalized_ratio(self) -> None:
        bbox = PageBBox(
            page=1,
            bbox=BBox(x0=61.2, y0=79.2, x1=306.0, y1=237.6),
            bbox_norm=NormalizedBBox(x=0.1, y=0.1, w=0.4, h=0.2),
        )

        projected = page_bbox_to_image_rect(bbox, image_width=1224, image_height=1584)

        expected = (122.4, 158.4, 612.0, 475.2)
        for actual, wanted in zip(projected, expected):
            self.assertAlmostEqual(actual, wanted)

    def test_union_page_bboxes_merges_multiple_fragments(self) -> None:
        first = PageBBox(
            page=3,
            bbox=BBox(x0=100.0, y0=200.0, x1=300.0, y1=500.0),
            bbox_norm=NormalizedBBox(x=0.1, y=0.2, w=0.2, h=0.3),
        )
        second = PageBBox(
            page=3,
            bbox=BBox(x0=280.0, y0=220.0, x1=620.0, y1=520.0),
            bbox_norm=NormalizedBBox(x=0.28, y=0.22, w=0.34, h=0.3),
        )

        merged = union_page_bboxes([first, second])

        self.assertEqual(merged.page, 3)
        self.assertAlmostEqual(merged.bbox.x0, 100.0)
        self.assertAlmostEqual(merged.bbox.x1, 620.0)
        self.assertAlmostEqual(merged.bbox_norm.x, 0.1)
        self.assertAlmostEqual(merged.bbox_norm.w, 0.52)

    def test_slice_page_bbox_horizontal_uses_overlapping_halves(self) -> None:
        bbox = PageBBox(
            page=3,
            bbox=BBox(x0=100.0, y0=200.0, x1=700.0, y1=800.0),
            bbox_norm=NormalizedBBox(x=0.1, y=0.2, w=0.6, h=0.4),
        )

        left = slice_page_bbox_horizontal(bbox, side="left")
        right = slice_page_bbox_horizontal(bbox, side="right")

        self.assertLess(left.bbox.x0, right.bbox.x0)
        self.assertGreater(left.bbox.x1, 400.0)
        self.assertLess(right.bbox.x0, 400.0)
        self.assertAlmostEqual(left.bbox.y0, bbox.bbox.y0)
        self.assertAlmostEqual(right.bbox.y1, bbox.bbox.y1)


if __name__ == "__main__":
    unittest.main()

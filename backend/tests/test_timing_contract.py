from __future__ import annotations

import unittest

from src.models import ActionTemplate, BBox, HighlightWord, NormalizedBBox
from src.services.analysis_service import _build_narration_words_and_beats


class TimingContractTests(unittest.TestCase):
    def test_duplicate_narration_words_map_to_matching_duplicate_highlights(self) -> None:
        bbox = BBox(x0=0, y0=0, x1=10, y1=10)
        bbox_norm = NormalizedBBox(x=0, y=0, w=0.1, h=0.1)
        highlights = [
            HighlightWord(
                highlight_id="highlight-00001",
                unit_id="unit-001",
                visual_target_id="target-001-01",
                order=1,
                source_word="attention",
                normalized_source_word="attention",
                source_occurrence=1,
                page=1,
                bbox=bbox,
                bbox_norm=bbox_norm,
            ),
            HighlightWord(
                highlight_id="highlight-00002",
                unit_id="unit-001",
                visual_target_id="target-001-01",
                order=2,
                source_word="attention",
                normalized_source_word="attention",
                source_occurrence=2,
                page=1,
                bbox=bbox,
                bbox_norm=bbox_norm,
            ),
        ]
        action = ActionTemplate(
            action_id="action-001-01",
            unit_id="unit-001",
            primitive="text_highlight",
            visual_target_id="target-001-01",
            narration_anchor="attention attention",
            spoken_text="Attention shifts attention.",
        )

        words, beats = _build_narration_words_and_beats(
            action=action,
            target_highlights=highlights,
            next_word_order=1,
            next_beat_order=1,
        )

        matched = [
            word.highlight_word_ids[0]
            for word in words
            if word.normalized_word == "attention"
        ]
        self.assertEqual(matched, ["highlight-00001", "highlight-00002"])
        self.assertEqual(
            [beat.highlight_word_ids for beat in beats if beat.highlight_word_ids],
            [["highlight-00001"], ["highlight-00002"]],
        )


if __name__ == "__main__":
    unittest.main()

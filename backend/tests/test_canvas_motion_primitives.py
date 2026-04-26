from __future__ import annotations

import unittest

from src.models import NormalizedBBox
from src.services.canvas_video_renderer import (
    CameraState,
    SceneAction,
    SceneSection,
    _active_camera,
    _scene_camera,
    _section_camera,
    _section_zoom_for_bbox,
    _section_for_time,
    _union_bbox,
)
from src.services.video_motion_primitives import (
    highlight_motion,
    primitive_motion_state,
    underline_motion,
    zoom_motion,
)


class CanvasMotionPrimitiveTests(unittest.TestCase):
    @staticmethod
    def _section(
        *,
        start_s: float = 1.0,
        stop_s: float = 4.0,
        focus_bbox_norm: NormalizedBBox | None = None,
    ) -> SceneSection:
        return SceneSection(
            section_id="section-1",
            section_title="Section 1",
            page=1,
            page_image_path="/tmp/page.png",
            start_s=start_s,
            stop_s=stop_s,
            narration_text="alpha beta gamma",
            segments=[],
            timed_actions=[],
            focus_bbox_norm=focus_bbox_norm or NormalizedBBox(x=0.25, y=0.2, w=0.32, h=0.22),
        )

    def test_underline_progress_advances_during_action(self) -> None:
        motion = underline_motion(now_s=1.5, start_s=1.0, stop_s=2.0, strength=1.0)
        self.assertGreater(motion.alpha, 0)
        self.assertGreater(motion.progress, 0.25)
        self.assertLessEqual(motion.progress, 1.0)

    def test_highlight_fades_out_after_action(self) -> None:
        during = highlight_motion(now_s=1.2, start_s=1.0, stop_s=2.0, strength=1.0)
        after = highlight_motion(now_s=2.3, start_s=1.0, stop_s=2.0, strength=1.0)
        self.assertGreater(during.alpha, after.alpha)

    def test_zoom_targets_small_bbox_more_aggressively(self) -> None:
        small = zoom_motion(
            bbox_norm=NormalizedBBox(x=0.1, y=0.1, w=0.06, h=0.04),
            now_s=1.4,
            start_s=1.0,
            stop_s=2.0,
            max_zoom=1.35,
        )
        large = zoom_motion(
            bbox_norm=NormalizedBBox(x=0.1, y=0.1, w=0.35, h=0.2),
            now_s=1.4,
            start_s=1.0,
            stop_s=2.0,
            max_zoom=1.35,
        )
        self.assertGreater(small.scale, large.scale)

    def test_text_highlight_combines_overlay_and_zoom(self) -> None:
        state = primitive_motion_state(
            primitive="text_highlight",
            bbox_norm=NormalizedBBox(x=0.3, y=0.2, w=0.12, h=0.05),
            now_s=1.25,
            start_s=1.0,
            stop_s=2.0,
        )
        self.assertGreater(state.highlight.alpha, 0)
        self.assertGreater(state.underline.alpha, 0)
        self.assertGreater(state.zoom.scale, 1.0)

    def test_union_bbox_covers_multiple_highlights(self) -> None:
        union = _union_bbox(
            [
                NormalizedBBox(x=0.1, y=0.2, w=0.1, h=0.05),
                NormalizedBBox(x=0.45, y=0.35, w=0.08, h=0.04),
            ]
        )
        self.assertLessEqual(union.x, 0.1)
        self.assertLessEqual(union.y, 0.2)
        self.assertGreaterEqual(union.x + union.w, 0.53)
        self.assertGreaterEqual(union.y + union.h, 0.39)

    def test_section_camera_zooms_into_section_focus(self) -> None:
        section = self._section(focus_bbox_norm=NormalizedBBox(x=0.1, y=0.15, w=0.2, h=0.18))

        before = _section_camera(section, now_s=0.6, frame_size=(1200, 1600))
        during = _section_camera(section, now_s=1.4, frame_size=(1200, 1600))

        self.assertAlmostEqual(before.scale, 1.0, places=2)
        self.assertGreater(during.scale, 1.0)
        self.assertAlmostEqual(during.center[0], 600.0, places=1)
        self.assertLess(during.center[1], 800.0)

    def test_section_zoom_targets_medium_bbox(self) -> None:
        zoom = _section_zoom_for_bbox(
            NormalizedBBox(x=0.2, y=0.18, w=0.46, h=0.24),
        )

        self.assertGreater(zoom, 1.5)

    def test_section_camera_holds_until_section_end(self) -> None:
        section = self._section()

        steady = _section_camera(section, now_s=2.2, frame_size=(1200, 1600))
        near_end = _section_camera(section, now_s=3.95, frame_size=(1200, 1600))

        self.assertAlmostEqual(steady.scale, near_end.scale, places=2)
        self.assertGreater(near_end.scale, 1.0)

    def test_active_camera_uses_section_focus_without_action(self) -> None:
        section = self._section(focus_bbox_norm=NormalizedBBox(x=0.58, y=0.12, w=0.18, h=0.16))

        camera = _active_camera(section, now_s=2.0, frame_size=(1200, 1600))

        self.assertIsInstance(camera, CameraState)
        self.assertGreater(camera.scale, 1.0)
        self.assertAlmostEqual(camera.center[0], 600.0, places=1)
        self.assertLess(camera.center[1], 800.0)

    def test_active_camera_keeps_horizontal_center_when_action_is_active(self) -> None:
        section = SceneSection(
            section_id="section-1",
            section_title="Section 1",
            page=1,
            page_image_path="/tmp/page.png",
            start_s=1.0,
            stop_s=4.0,
            narration_text="alpha beta gamma",
            segments=[],
            timed_actions=[
                SceneAction(
                    action_id="action-1",
                    section_id="section-1",
                    section_title="Section 1",
                    page=1,
                    word="alpha",
                    occurrence=1,
                    primitive="figure_callout",
                    start_s=1.4,
                    stop_s=2.4,
                    bbox_norm=NormalizedBBox(x=0.08, y=0.24, w=0.12, h=0.05),
                    action_text="Call out alpha",
                    narration_word="alpha",
                    narration_occurrence=1,
                )
            ],
            focus_bbox_norm=NormalizedBBox(x=0.12, y=0.2, w=0.3, h=0.2),
        )

        section_camera = _section_camera(section, now_s=1.8, frame_size=(1200, 1600))
        camera = _active_camera(section, now_s=1.8, frame_size=(1200, 1600))

        self.assertAlmostEqual(camera.center[0], 600.0, places=1)
        self.assertGreater(camera.scale, section_camera.scale)

    def test_section_selection_switches_at_next_start(self) -> None:
        first = self._section(start_s=0.0, stop_s=2.0)
        second = SceneSection(
            section_id="section-2",
            section_title="Section 2",
            page=1,
            page_image_path="/tmp/page.png",
            start_s=2.0,
            stop_s=4.0,
            narration_text="delta epsilon",
            segments=[],
            timed_actions=[],
            focus_bbox_norm=NormalizedBBox(x=0.62, y=0.3, w=0.18, h=0.15),
        )

        self.assertEqual(_section_for_time([first, second], 1.99).section_id, "section-1")
        self.assertEqual(_section_for_time([first, second], 2.0).section_id, "section-2")

    def test_scene_camera_holds_section_focus_for_full_section(self) -> None:
        section = self._section(start_s=1.0, stop_s=4.0)

        during = _scene_camera([section], 2.6, (1200, 1600))
        near_end = _scene_camera([section], 3.98, (1200, 1600))

        self.assertAlmostEqual(during.scale, near_end.scale, places=2)
        self.assertAlmostEqual(during.center[0], near_end.center[0], places=1)
        self.assertAlmostEqual(during.center[1], near_end.center[1], places=1)

    def test_scene_camera_transitions_between_sections(self) -> None:
        first = self._section(
            start_s=0.0,
            stop_s=2.0,
            focus_bbox_norm=NormalizedBBox(x=0.1, y=0.12, w=0.2, h=0.16),
        )
        second = SceneSection(
            section_id="section-2",
            section_title="Section 2",
            page=1,
            page_image_path="/tmp/page.png",
            start_s=2.0,
            stop_s=4.0,
            narration_text="delta epsilon",
            segments=[],
            timed_actions=[],
            focus_bbox_norm=NormalizedBBox(x=0.62, y=0.38, w=0.18, h=0.15),
        )

        at_boundary = _scene_camera([first, second], 2.0, (1200, 1600))
        after_transition = _scene_camera([first, second], 2.4, (1200, 1600))

        self.assertAlmostEqual(at_boundary.center[0], 600.0, places=1)
        self.assertAlmostEqual(after_transition.center[0], 600.0, places=1)
        self.assertGreater(after_transition.center[1], at_boundary.center[1])


if __name__ == "__main__":
    unittest.main()

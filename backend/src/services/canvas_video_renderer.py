from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.models import NormalizedBBox, Primitive
from src.services.video_motion_primitives import (
    clamp,
    ease_in_out_sine,
    primitive_motion_state,
    target_zoom_for_bbox,
)


RESAMPLING = getattr(Image, "Resampling", Image)
SECTION_CAMERA_MAX_ZOOM = 2.4
SECTION_CAMERA_LEAD_IN_S = 0.3
SECTION_CAMERA_LEAD_OUT_S = 0.28
ACTION_CAMERA_BLEND = 0.18
ACTION_CAMERA_RELATIVE_ZOOM = 0.55
ACTION_CAMERA_MAX_SCALE = 2.7
SECTION_CAMERA_TARGET_FILL = 0.58
SECTION_CAMERA_HORIZONTAL_SAFE_FILL = 0.94


@dataclass(frozen=True)
class SceneAction:
    action_id: str
    section_id: str
    section_title: str
    page: int
    word: str
    occurrence: int
    primitive: Primitive
    start_s: float
    stop_s: float
    bbox_norm: NormalizedBBox
    action_text: str
    narration_word: str
    narration_occurrence: int


@dataclass(frozen=True)
class SceneSection:
    section_id: str
    section_title: str
    page: int
    page_image_path: str
    start_s: float
    stop_s: float
    narration_text: str
    segments: list[dict[str, Any]]
    timed_actions: list[SceneAction]
    focus_bbox_norm: NormalizedBBox


@dataclass(frozen=True)
class CameraState:
    scale: float
    center: tuple[float, float]


def _coerce_bbox(payload: dict[str, Any]) -> NormalizedBBox:
    return NormalizedBBox(
        x=float(payload["x"]),
        y=float(payload["y"]),
        w=float(payload["w"]),
        h=float(payload["h"]),
    )


def _coerce_action(payload: dict[str, Any]) -> SceneAction:
    return SceneAction(
        action_id=str(payload.get("action_id") or payload.get("word") or "action"),
        section_id=str(payload["section_id"]),
        section_title=str(payload.get("section_title", payload["section_id"])),
        page=int(payload["page"]),
        word=str(payload["word"]),
        occurrence=int(payload.get("occurrence", 1)),
        primitive=str(payload.get("primitive", "text_highlight")),
        start_s=float(payload["start_s"]),
        stop_s=float(payload["stop_s"]),
        bbox_norm=_coerce_bbox(payload["bbox_norm"]),
        action_text=str(payload.get("action_text", payload.get("word", ""))),
        narration_word=str(payload.get("narration_word", payload.get("word", ""))),
        narration_occurrence=int(payload.get("narration_occurrence", 1)),
    )


def _coerce_section(payload: dict[str, Any]) -> SceneSection:
    actions = [_coerce_action(item) for item in payload.get("timed_actions", [])]
    focus_bbox_payload = payload.get("focus_bbox_norm")
    if focus_bbox_payload is None:
        focus_bbox = _union_bbox([action.bbox_norm for action in actions])
    else:
        focus_bbox = _coerce_bbox(focus_bbox_payload)
    return SceneSection(
        section_id=str(payload["section_id"]),
        section_title=str(payload.get("section_title", payload["section_id"])),
        page=int(payload["page"]),
        page_image_path=str(payload["page_image_path"]),
        start_s=float(payload["start_s"]),
        stop_s=float(payload["stop_s"]),
        narration_text=str(payload.get("narration_text", "")),
        segments=list(payload.get("segments", [])),
        timed_actions=actions,
        focus_bbox_norm=focus_bbox,
    )


def _pad_bbox(bbox: NormalizedBBox, *, pad_x: float = 0.05, pad_y: float = 0.08) -> NormalizedBBox:
    x0 = clamp(bbox.x - pad_x, 0.0, 1.0)
    y0 = clamp(bbox.y - pad_y, 0.0, 1.0)
    x1 = clamp(bbox.x + bbox.w + pad_x, 0.0, 1.0)
    y1 = clamp(bbox.y + bbox.h + pad_y, 0.0, 1.0)
    return NormalizedBBox(x=x0, y=y0, w=max(0.01, x1 - x0), h=max(0.01, y1 - y0))


def _union_bbox(boxes: list[NormalizedBBox]) -> NormalizedBBox:
    if not boxes:
        return NormalizedBBox(x=0.0, y=0.0, w=1.0, h=1.0)
    x0 = min(box.x for box in boxes)
    y0 = min(box.y for box in boxes)
    x1 = max(box.x + box.w for box in boxes)
    y1 = max(box.y + box.h for box in boxes)
    return _pad_bbox(NormalizedBBox(x=x0, y=y0, w=x1 - x0, h=y1 - y0))


def _bbox_center(bbox: NormalizedBBox, frame_size: tuple[int, int]) -> tuple[float, float]:
    width, height = frame_size
    return (
        (bbox.x + bbox.w / 2.0) * width,
        (bbox.y + bbox.h / 2.0) * height,
    )


def _lerp(start: float, stop: float, progress: float) -> float:
    return start + (stop - start) * progress


def _blend_camera(start: CameraState, stop: CameraState, progress: float) -> CameraState:
    return CameraState(
        scale=_lerp(start.scale, stop.scale, progress),
        center=(
            _lerp(start.center[0], stop.center[0], progress),
            _lerp(start.center[1], stop.center[1], progress),
        ),
    )


def _fit_page(image_path: str, frame_size: tuple[int, int]) -> Image.Image:
    image = Image.open(image_path).convert("RGBA")
    return ImageOps.fit(image, frame_size, method=RESAMPLING.LANCZOS)


def _active_segment_text(section: SceneSection, now_s: float) -> str:
    for segment in section.segments:
        if float(segment["start_s"]) <= now_s <= float(segment["stop_s"]):
            text = str(segment.get("text", "")).strip()
            if text:
                return text
    return section.narration_text


def _bbox_rect(bbox: NormalizedBBox, frame_size: tuple[int, int]) -> tuple[float, float, float, float]:
    width, height = frame_size
    return (
        bbox.x * width,
        bbox.y * height,
        (bbox.x + bbox.w) * width,
        (bbox.y + bbox.h) * height,
    )


def _draw_dimming(image: Image.Image, alpha: int) -> None:
    if alpha <= 0:
        return
    overlay = Image.new("RGBA", image.size, (8, 12, 24, alpha))
    image.alpha_composite(overlay)


def _draw_action_overlay(
    image: Image.Image,
    *,
    action: SceneAction,
    now_s: float,
) -> tuple[float, tuple[float, float]]:
    state = primitive_motion_state(
        primitive=action.primitive,
        bbox_norm=action.bbox_norm,
        now_s=now_s,
        start_s=action.start_s,
        stop_s=action.stop_s,
    )
    if state.intensity <= 0.0:
        x0, y0, x1, y1 = _bbox_rect(action.bbox_norm, image.size)
        return 1.0, ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    x0, y0, x1, y1 = _bbox_rect(action.bbox_norm, image.size)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    draw = ImageDraw.Draw(image, "RGBA")

    if state.highlight.alpha > 0 and action.primitive != "text_highlight":
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=max(8, int(min(x1 - x0, y1 - y0) * 0.12)),
            outline=(255, 199, 0, state.highlight.border_alpha),
            width=max(2, int(max(image.size) * 0.0035)),
        )

    if state.callout.alpha > 0:
        pulse_x = (x1 - x0) * (state.callout.pulse_scale - 1.0) / 2.0
        pulse_y = (y1 - y0) * (state.callout.pulse_scale - 1.0) / 2.0
        draw.rounded_rectangle(
            (x0 - pulse_x, y0 - pulse_y, x1 + pulse_x, y1 + pulse_y),
            radius=max(10, int(min(x1 - x0, y1 - y0) * 0.16)),
            outline=(59, 130, 246, state.callout.alpha),
            width=max(3, int(max(image.size) * 0.004)),
        )

    if state.underline.alpha > 0 and state.underline.progress > 0:
        underline_y = y1 + max(4, int(image.size[1] * 0.008))
        underline_end = x0 + (x1 - x0) * state.underline.progress
        draw.line(
            (x0, underline_y, underline_end, underline_y),
            fill=(239, 68, 68, state.underline.alpha),
            width=max(3, int(image.size[1] * 0.006)),
        )

    return state.zoom.scale, (cx, cy)


def _zoom_frame(
    image: Image.Image,
    *,
    scale: float,
    center: tuple[float, float],
) -> Image.Image:
    if scale <= 1.001:
        return image
    width, height = image.size
    scaled = image.resize(
        (max(width, int(round(width * scale))), max(height, int(round(height * scale)))),
        RESAMPLING.LANCZOS,
    )
    center_x = center[0] * scale
    center_y = center[1] * scale
    left = clamp(center_x - width / 2.0, 0.0, max(0.0, scaled.size[0] - width))
    top = clamp(center_y - height / 2.0, 0.0, max(0.0, scaled.size[1] - height))
    return scaled.crop(
        (
            int(round(left)),
            int(round(top)),
            int(round(left + width)),
            int(round(top + height)),
        )
    )


def _draw_title_and_caption(
    image: Image.Image,
    *,
    title: str,
    caption: str,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(16, int(height * 0.018)))
        caption_font = ImageFont.truetype("DejaVuSans.ttf", max(42, int(height * 0.06)))
    except OSError:
        title_font = ImageFont.load_default()
        caption_font = ImageFont.load_default()

    title_box = (24, 18, min(width - 24, width * 0.72), 52)
    draw.rounded_rectangle(title_box, radius=14, fill=(15, 23, 42, 188))
    draw.text((38, 28), title.strip()[:90], fill=(248, 250, 252, 255), font=title_font)

    clean_caption = " ".join(caption.split()).strip()
    if not clean_caption:
        return
    wrapped = textwrap.fill(clean_caption, width=max(14, min(26, width // 52)))
    lines = wrapped.splitlines()[:5]
    text = "\n".join(lines)
    text_bbox = draw.multiline_textbbox((0, 0), text, font=caption_font, spacing=12)
    box_width = min(width - 48, (text_bbox[2] - text_bbox[0]) + 56)
    box_height = (text_bbox[3] - text_bbox[1]) + 48
    x0 = 24
    y0 = height - box_height - 24
    draw.rounded_rectangle(
        (x0, y0, x0 + box_width, y0 + box_height),
        radius=16,
        fill=(255, 255, 255, 236),
        outline=(0, 0, 0, 255),
        width=2,
    )
    draw.multiline_text(
        (x0 + 28, y0 + 22),
        text,
        fill=(0, 0, 0, 255),
        font=caption_font,
        spacing=12,
    )


def _page_camera(frame_size: tuple[int, int]) -> CameraState:
    return CameraState(
        scale=1.0,
        center=(frame_size[0] / 2.0, frame_size[1] / 2.0),
    )


def _section_zoom_for_bbox(
    bbox: NormalizedBBox,
    *,
    max_zoom: float = SECTION_CAMERA_MAX_ZOOM,
    min_zoom: float = 1.0,
    target_fill: float = SECTION_CAMERA_TARGET_FILL,
    center_x_norm: float = 0.5,
    horizontal_safe_fill: float = SECTION_CAMERA_HORIZONTAL_SAFE_FILL,
) -> float:
    height = max(bbox.h, 0.01)
    desired_vertical = target_fill / height
    x0 = bbox.x
    x1 = bbox.x + bbox.w
    max_distance_from_center = max(
        abs(x0 - center_x_norm),
        abs(x1 - center_x_norm),
        0.01,
    )
    horizontal_limit = (0.5 * horizontal_safe_fill) / max_distance_from_center
    desired = min(desired_vertical, horizontal_limit)
    return clamp(desired, min_zoom, max_zoom)


def _focus_camera(
    bbox: NormalizedBBox,
    *,
    frame_size: tuple[int, int],
    max_zoom: float = SECTION_CAMERA_MAX_ZOOM,
) -> CameraState:
    return CameraState(
        scale=_section_zoom_for_bbox(
            bbox,
            max_zoom=max_zoom,
            min_zoom=1.0,
        ),
        center=(
            frame_size[0] / 2.0,
            _bbox_center(bbox, frame_size)[1],
        ),
    )


def _section_camera(section: SceneSection, now_s: float, frame_size: tuple[int, int]) -> CameraState:
    if section.stop_s <= section.start_s:
        return _page_camera(frame_size)
    if now_s <= section.start_s:
        return _page_camera(frame_size)
    focus_camera = _focus_camera(section.focus_bbox_norm, frame_size=frame_size)
    if now_s >= section.start_s + SECTION_CAMERA_LEAD_IN_S:
        return focus_camera
    progress = ease_in_out_sine(
        clamp((now_s - section.start_s) / max(SECTION_CAMERA_LEAD_IN_S, 1e-6), 0.0, 1.0)
    )
    return _blend_camera(_page_camera(frame_size), focus_camera, progress)


def _active_camera(section: SceneSection, now_s: float, frame_size: tuple[int, int]) -> CameraState:
    base_camera = _section_camera(section, now_s, frame_size)
    best_action: SceneAction | None = None
    best_score = 0.0
    for action in section.timed_actions:
        state = primitive_motion_state(
            primitive=action.primitive,
            bbox_norm=action.bbox_norm,
            now_s=now_s,
            start_s=action.start_s,
            stop_s=action.stop_s,
        )
        if state.intensity <= 0.0:
            continue
        score = state.intensity * state.zoom.scale
        if score > best_score:
            best_score = score
            best_action = action
    if best_action is None:
        return base_camera

    state = primitive_motion_state(
        primitive=best_action.primitive,
        bbox_norm=best_action.bbox_norm,
        now_s=now_s,
        start_s=best_action.start_s,
        stop_s=best_action.stop_s,
    )
    relative_zoom = 1.0 + max(0.0, state.zoom.scale - 1.0) * ACTION_CAMERA_RELATIVE_ZOOM
    action_camera = CameraState(
        scale=clamp(base_camera.scale * relative_zoom, base_camera.scale, ACTION_CAMERA_MAX_SCALE),
        center=base_camera.center,
    )
    blend = ACTION_CAMERA_BLEND * ease_in_out_sine(clamp(state.intensity, 0.0, 1.0))
    return _blend_camera(base_camera, action_camera, blend)


def _render_frame(
    *,
    base_page: Image.Image,
    section: SceneSection,
    now_s: float,
    camera: CameraState | None = None,
    show_caption: bool = True,
) -> Image.Image:
    frame = base_page.copy()
    frame_actions = [action for action in section.timed_actions if action.start_s - 0.16 <= now_s <= action.stop_s + 0.24]
    for action in sorted(frame_actions, key=lambda item: (item.start_s, item.stop_s)):
        _draw_action_overlay(frame, action=action, now_s=now_s)
    selected_camera = camera or _active_camera(section, now_s, frame.size)
    frame = _zoom_frame(frame, scale=selected_camera.scale, center=selected_camera.center)
    if show_caption:
        _draw_title_and_caption(
            frame,
            title=section.section_title,
            caption=_active_segment_text(section, now_s),
        )
    return frame.convert("RGB")


def _section_focus_boxes(scene_data: dict[str, Any]) -> dict[str, NormalizedBBox]:
    focus_boxes: dict[str, list[NormalizedBBox]] = {}
    for action in scene_data.get("timed_actions", []):
        section_id = str(action["section_id"])
        focus_boxes.setdefault(section_id, []).append(_coerce_bbox(action["bbox_norm"]))
    return {section_id: _union_bbox(boxes) for section_id, boxes in focus_boxes.items()}


def _section_for_time(sections: list[SceneSection], now_s: float) -> SceneSection:
    if not sections:
        raise RuntimeError("Scene data does not contain any sections.")
    for index, section in enumerate(sections):
        next_start_s = sections[index + 1].start_s if index + 1 < len(sections) else float("inf")
        if section.start_s <= now_s < next_start_s:
            return section
    if now_s < sections[0].start_s:
        return sections[0]
    return sections[-1]


def _scene_camera(sections: list[SceneSection], now_s: float, frame_size: tuple[int, int]) -> CameraState:
    page_camera = _page_camera(frame_size)
    if not sections:
        return page_camera
    active_section = _section_for_time(sections, now_s)
    active_index = sections.index(active_section)
    active_focus_camera = _focus_camera(active_section.focus_bbox_norm, frame_size=frame_size)

    if now_s < active_section.start_s:
        return page_camera
    if now_s < active_section.start_s + SECTION_CAMERA_LEAD_IN_S:
        source_camera = (
            _focus_camera(sections[active_index - 1].focus_bbox_norm, frame_size=frame_size)
            if active_index > 0
            else page_camera
        )
        intro_progress = ease_in_out_sine(
            clamp(
                (now_s - active_section.start_s) / max(SECTION_CAMERA_LEAD_IN_S, 1e-6),
                0.0,
                1.0,
            )
        )
        return _blend_camera(source_camera, active_focus_camera, intro_progress)

    if active_index == len(sections) - 1 and now_s > active_section.stop_s:
        outro_progress = ease_in_out_sine(
            clamp(
                (now_s - active_section.stop_s) / max(SECTION_CAMERA_LEAD_OUT_S, 1e-6),
                0.0,
                1.0,
            )
        )
        return _blend_camera(active_focus_camera, page_camera, outro_progress)

    return active_focus_camera


def render_scene_data_to_mp4(
    *,
    scene_data: dict[str, Any],
    output_path: Path,
    fps: int = 24,
) -> Path:
    focus_boxes = _section_focus_boxes(scene_data)
    scene_sections = []
    for item in scene_data.get("sections", []):
        if "focus_bbox_norm" not in item and item.get("section_id") in focus_boxes:
            item = {**item, "focus_bbox_norm": focus_boxes[str(item["section_id"])].model_dump(mode="json")}
        scene_sections.append(item)
    sections = [_coerce_section(item) for item in scene_sections]
    if not sections:
        raise RuntimeError("Scene data does not contain any sections.")
    page_image_paths = {section.page_image_path for section in sections}
    if len(page_image_paths) != 1:
        raise RuntimeError(
            "Canvas renderer currently supports a single page per video. "
            "Pass scene data for one page only."
        )

    frame_size_payload = scene_data.get("frame_size") or {}
    if frame_size_payload:
        frame_size = (
            int(frame_size_payload["width"]),
            int(frame_size_payload["height"]),
        )
    else:
        first_page = Image.open(sections[0].page_image_path)
        frame_size = first_page.size
        first_page.close()

    duration_s = float(scene_data["duration_s"])
    audio_path_raw = scene_data.get("final_audio_path") or scene_data.get("audio_path")
    audio_path = Path(audio_path_raw).resolve() if audio_path_raw else None
    page_cache: dict[str, Image.Image] = {
        section.page_image_path: _fit_page(section.page_image_path, frame_size)
        for section in sections
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="paperazzi-canvas-frames-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        frame_count = max(1, int(duration_s * fps) + 1)

        for frame_index in range(frame_count):
            now_s = min(duration_s, frame_index / fps)
            section = _section_for_time(sections, now_s)
            camera = _scene_camera(sections, now_s, frame_size)
            frame = _render_frame(
                base_page=page_cache[section.page_image_path],
                section=section,
                now_s=now_s,
                camera=camera,
            )
            frame.save(temp_dir / f"frame-{frame_index:06d}.png", format="PNG")

        encode_command = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(temp_dir / "frame-%06d.png"),
        ]
        if audio_path is not None:
            encode_command.extend(["-i", str(audio_path)])
        encode_command.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if audio_path is not None:
            encode_command.extend(
                [
                    "-c:a",
                    "aac",
                    "-shortest",
                ]
            )
        encode_command.append(str(output_path))
        completed = subprocess.run(
            encode_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg render failed:\n" + completed.stdout[-4000:])

    return output_path


def render_scene_file_to_mp4(
    *,
    scene_data_path: Path,
    output_path: Path,
    fps: int = 24,
) -> Path:
    payload = json.loads(scene_data_path.read_text(encoding="utf-8"))
    return render_scene_data_to_mp4(scene_data=payload, output_path=output_path, fps=fps)

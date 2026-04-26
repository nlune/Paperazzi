from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi

from src.models import NormalizedBBox, Primitive


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def ease_out_cubic(value: float) -> float:
    value = clamp(value, 0.0, 1.0)
    return 1.0 - (1.0 - value) ** 3


def ease_in_out_sine(value: float) -> float:
    value = clamp(value, 0.0, 1.0)
    return -(cos(pi * value) - 1.0) / 2.0


@dataclass(frozen=True)
class HighlightMotion:
    alpha: int
    border_alpha: int


@dataclass(frozen=True)
class UnderlineMotion:
    alpha: int
    progress: float


@dataclass(frozen=True)
class ZoomMotion:
    scale: float


@dataclass(frozen=True)
class CalloutMotion:
    alpha: int
    pulse_scale: float


@dataclass(frozen=True)
class MotionState:
    intensity: float
    highlight: HighlightMotion
    underline: UnderlineMotion
    zoom: ZoomMotion
    callout: CalloutMotion
    dim_alpha: int


@dataclass(frozen=True)
class PrimitiveMotionProfile:
    max_zoom: float
    highlight_strength: float
    underline_strength: float
    callout_strength: float
    dim_strength: float


PRIMITIVE_MOTION_PROFILES: dict[Primitive, PrimitiveMotionProfile] = {
    "text_highlight": PrimitiveMotionProfile(
        max_zoom=1.18,
        highlight_strength=1.0,
        underline_strength=1.0,
        callout_strength=0.45,
        dim_strength=0.2,
    ),
    "page_zoom_pan": PrimitiveMotionProfile(
        max_zoom=1.32,
        highlight_strength=0.35,
        underline_strength=0.0,
        callout_strength=0.3,
        dim_strength=0.12,
    ),
    "figure_callout": PrimitiveMotionProfile(
        max_zoom=1.28,
        highlight_strength=0.45,
        underline_strength=0.0,
        callout_strength=1.0,
        dim_strength=0.24,
    ),
    "equation_steps": PrimitiveMotionProfile(
        max_zoom=1.24,
        highlight_strength=0.75,
        underline_strength=0.55,
        callout_strength=0.85,
        dim_strength=0.18,
    ),
    "split_explain": PrimitiveMotionProfile(
        max_zoom=1.22,
        highlight_strength=0.7,
        underline_strength=0.25,
        callout_strength=0.7,
        dim_strength=0.18,
    ),
    "section_scroll": PrimitiveMotionProfile(
        max_zoom=1.08,
        highlight_strength=0.0,
        underline_strength=0.0,
        callout_strength=0.0,
        dim_strength=0.0,
    ),
    "page_transition": PrimitiveMotionProfile(
        max_zoom=1.04,
        highlight_strength=0.0,
        underline_strength=0.0,
        callout_strength=0.0,
        dim_strength=0.0,
    ),
}


def motion_intensity(
    *,
    now_s: float,
    start_s: float,
    stop_s: float,
    lead_in_s: float = 0.12,
    trail_out_s: float = 0.18,
) -> float:
    if stop_s <= start_s:
        return 0.0
    if now_s < start_s - lead_in_s or now_s > stop_s + trail_out_s:
        return 0.0
    if now_s < start_s:
        progress = (now_s - (start_s - lead_in_s)) / max(lead_in_s, 1e-6)
        return ease_out_cubic(progress)
    if now_s <= stop_s:
        return 1.0
    progress = 1.0 - ((now_s - stop_s) / max(trail_out_s, 1e-6))
    return ease_in_out_sine(progress)


def target_zoom_for_bbox(
    bbox_norm: NormalizedBBox,
    *,
    max_zoom: float,
    min_zoom: float = 1.03,
) -> float:
    focus_span = max(bbox_norm.w, bbox_norm.h, 0.05)
    desired = 0.34 / focus_span
    return clamp(desired, min_zoom, max_zoom)


def highlight_motion(
    *,
    now_s: float,
    start_s: float,
    stop_s: float,
    strength: float = 1.0,
) -> HighlightMotion:
    intensity = motion_intensity(now_s=now_s, start_s=start_s, stop_s=stop_s)
    return HighlightMotion(
        alpha=int(110 * clamp(intensity * strength, 0.0, 1.0)),
        border_alpha=int(220 * clamp(intensity * strength, 0.0, 1.0)),
    )


def underline_motion(
    *,
    now_s: float,
    start_s: float,
    stop_s: float,
    strength: float = 1.0,
) -> UnderlineMotion:
    intensity = motion_intensity(now_s=now_s, start_s=start_s, stop_s=stop_s)
    duration = max(stop_s - start_s, 1e-6)
    raw_progress = (now_s - start_s) / duration
    progress = ease_out_cubic(clamp(raw_progress * 1.45, 0.0, 1.0))
    return UnderlineMotion(
        alpha=int(255 * clamp(intensity * strength, 0.0, 1.0)),
        progress=progress if intensity > 0.0 else 0.0,
    )


def zoom_motion(
    *,
    bbox_norm: NormalizedBBox,
    now_s: float,
    start_s: float,
    stop_s: float,
    max_zoom: float,
) -> ZoomMotion:
    intensity = motion_intensity(now_s=now_s, start_s=start_s, stop_s=stop_s)
    target = target_zoom_for_bbox(bbox_norm, max_zoom=max_zoom)
    return ZoomMotion(scale=1.0 + (target - 1.0) * ease_in_out_sine(intensity))


def callout_motion(
    *,
    now_s: float,
    start_s: float,
    stop_s: float,
    strength: float = 1.0,
) -> CalloutMotion:
    intensity = motion_intensity(now_s=now_s, start_s=start_s, stop_s=stop_s)
    pulse = 1.0 + 0.03 * ease_in_out_sine(intensity)
    return CalloutMotion(
        alpha=int(255 * clamp(intensity * strength, 0.0, 1.0)),
        pulse_scale=pulse,
    )


def primitive_motion_state(
    *,
    primitive: Primitive,
    bbox_norm: NormalizedBBox,
    now_s: float,
    start_s: float,
    stop_s: float,
) -> MotionState:
    profile = PRIMITIVE_MOTION_PROFILES[primitive]
    intensity = motion_intensity(now_s=now_s, start_s=start_s, stop_s=stop_s)
    highlight = highlight_motion(
        now_s=now_s,
        start_s=start_s,
        stop_s=stop_s,
        strength=profile.highlight_strength,
    )
    underline = underline_motion(
        now_s=now_s,
        start_s=start_s,
        stop_s=stop_s,
        strength=profile.underline_strength,
    )
    zoom = zoom_motion(
        bbox_norm=bbox_norm,
        now_s=now_s,
        start_s=start_s,
        stop_s=stop_s,
        max_zoom=profile.max_zoom,
    )
    callout = callout_motion(
        now_s=now_s,
        start_s=start_s,
        stop_s=stop_s,
        strength=profile.callout_strength,
    )
    return MotionState(
        intensity=intensity,
        highlight=highlight,
        underline=underline,
        zoom=zoom,
        callout=callout,
        dim_alpha=int(90 * clamp(intensity * profile.dim_strength, 0.0, 1.0)),
    )

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Stage = Literal[
    "created",
    "extracting_document",
    "pages_ready",
    "planning_sections",
    "analysis_ready",
    "generating_voice",
    "voice_ready",
    "rendering_video",
    "video_ready",
    "failed",
]

Primitive = Literal[
    "text_highlight",
    "page_zoom_pan",
    "figure_callout",
    "equation_steps",
    "split_explain",
    "section_scroll",
    "page_transition",
]

TargetKind = Literal["text", "figure", "equation", "table", "section"]
DecisionSource = Literal["auto", "mock", "gemini"]
TransitionKind = Literal["section_scroll", "page_transition"]
SectionRole = Literal[
    "body",
    "intro",
    "references",
    "appendix",
    "acknowledgements",
    "other",
]


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class NormalizedBBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class PageBBox(BaseModel):
    page: int
    bbox: BBox
    bbox_norm: NormalizedBBox


class SectionItem(BaseModel):
    item_id: str
    kind: str
    page_hint: int | None = None
    text: str


class SectionRecord(BaseModel):
    section_id: str
    order: int
    title: str
    level: int
    heading_path: list[str]
    section_role: SectionRole = "body"
    page_start: int
    page_end: int
    docling_refs: list[str] = Field(default_factory=list)
    page_bboxes: list[PageBBox] = Field(default_factory=list)
    text_excerpt: str
    section_text: str
    char_count: int
    included: bool = True
    skip_reason: str | None = None
    llm_use_section: bool | None = None
    llm_decision_reason: str | None = None
    llm_split_required: bool = False
    llm_split_reason: str | None = None
    split_into_unit_ids: list[str] = Field(default_factory=list)
    section_items: list[SectionItem] = Field(default_factory=list)


class SectionDecision(BaseModel):
    section_id: str
    use_section: bool
    reason: str
    source: DecisionSource
    split_required: bool = False
    split_reason: str | None = None


class DocumentPage(BaseModel):
    page: int
    width: float
    height: float
    image_path: str


class WordBox(BaseModel):
    index: int
    page: int | None = None
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    bbox_norm: NormalizedBBox | None = None
    block_no: int
    line_no: int
    word_no: int


class WordIndexPage(BaseModel):
    page: int
    width: float
    height: float
    words: list[WordBox]


class VisualTarget(BaseModel):
    target_id: str
    unit_id: str
    kind: TargetKind
    label: str
    page: int
    anchor_text: str
    match_strategy: str
    docling_ref: str | None = None
    word_refs: list[int] = Field(default_factory=list)
    fragments: list[PageBBox] = Field(default_factory=list)
    union_bbox: BBox
    union_bbox_norm: NormalizedBBox
    confidence: float
    fallback_used: bool = False


class SectionWordRef(BaseModel):
    word_ref_id: str
    section_id: str
    page: int
    word_index: int
    text: str
    bbox: BBox
    bbox_norm: NormalizedBBox
    source: Literal["pymupdf_contained"] = "pymupdf_contained"


class HighlightWord(BaseModel):
    highlight_id: str
    unit_id: str
    visual_target_id: str
    order: int
    source_word: str
    normalized_source_word: str
    source_occurrence: int
    page: int
    word_index: int | None = None
    section_word_ref_id: str | None = None
    bbox: BBox
    bbox_norm: NormalizedBBox
    action_ids: list[str] = Field(default_factory=list)


class NarrationWord(BaseModel):
    narration_word_id: str
    unit_id: str
    action_id: str
    visual_target_id: str
    order: int
    action_word_index: int
    word: str
    normalized_word: str
    occurrence: int
    highlight_word_ids: list[str] = Field(default_factory=list)
    start_s: float | None = None
    stop_s: float | None = None


class AnimationBeat(BaseModel):
    beat_id: str
    unit_id: str
    action_id: str
    visual_target_id: str
    primitive: Primitive
    order: int
    narration_word_id: str
    narration_word: str
    normalized_narration_word: str
    highlight_word_ids: list[str] = Field(default_factory=list)
    action_hint: Literal["highlight_word", "hold_target"] = "hold_target"
    start_s: float | None = None
    stop_s: float | None = None


class ActionTemplate(BaseModel):
    action_id: str
    unit_id: str
    primitive: Primitive
    visual_target_id: str
    narration_anchor: str
    spoken_text: str
    timing_policy: dict[str, object] = Field(default_factory=dict)
    effect_profile: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, object] = Field(default_factory=dict)
    highlight_word_ids: list[str] = Field(default_factory=list)
    narration_word_ids: list[str] = Field(default_factory=list)


class NarratedUnit(BaseModel):
    unit_id: str
    order: int
    source_section_ids: list[str]
    title: str
    goal: str
    narration_text: str
    summary_caption: str
    primitive_sequence: list[Primitive]
    primary_page: int
    page_span: list[int]
    focus_bbox: PageBBox
    visual_target_ids: list[str]
    action_ids: list[str]
    estimated_duration_s: float


class TransitionPlan(BaseModel):
    transition_id: str
    order: int
    transition_type: TransitionKind
    from_unit_id: str | None = None
    to_unit_id: str
    from_page: int | None = None
    to_page: int
    target_section_id: str
    target_bbox: PageBBox
    duration_s: float = 0.65
    start_s: float | None = None
    stop_s: float | None = None


class AnalysisRecord(BaseModel):
    analysis_version: str = "v1"
    project_id: str
    status: Literal["analysis_ready"] = "analysis_ready"
    created_at: datetime
    models: dict[str, str]
    document: dict[str, str | int | list[DocumentPage]]
    defaults: dict[str, str | int | float | bool]
    sections: list[SectionRecord]
    section_decisions: list[SectionDecision] = Field(default_factory=list)
    section_words: list[SectionWordRef] = Field(default_factory=list)
    narrated_units: list[NarratedUnit]
    visual_targets: list[VisualTarget]
    highlight_words: list[HighlightWord] = Field(default_factory=list)
    narration_words: list[NarrationWord] = Field(default_factory=list)
    animation_beats: list[AnimationBeat] = Field(default_factory=list)
    transitions: list[TransitionPlan] = Field(default_factory=list)
    action_templates: list[ActionTemplate]
    warnings: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class TimedText(BaseModel):
    text: str
    start_s: float
    stop_s: float
    client_req_id: str | None = None


class TimedAction(BaseModel):
    action_id: str
    unit_id: str | None = None
    start_s: float
    stop_s: float
    spoken_text: str
    primitive: Primitive
    visual_target_id: str
    highlight_word_ids: list[str] = Field(default_factory=list)
    narration_word_ids: list[str] = Field(default_factory=list)


class TimedNarrationWord(BaseModel):
    narration_word_id: str
    unit_id: str
    action_id: str
    visual_target_id: str
    word: str
    normalized_word: str
    occurrence: int
    start_s: float
    stop_s: float
    highlight_word_ids: list[str] = Field(default_factory=list)


class TimedAnimationBeat(BaseModel):
    beat_id: str
    unit_id: str
    action_id: str
    visual_target_id: str
    primitive: Primitive
    narration_word_id: str
    start_s: float
    stop_s: float
    highlight_word_ids: list[str] = Field(default_factory=list)
    action_hint: Literal["highlight_word", "hold_target"] = "hold_target"


class TimedTransition(BaseModel):
    transition_id: str
    transition_type: TransitionKind
    from_unit_id: str | None = None
    to_unit_id: str
    from_page: int | None = None
    to_page: int
    target_section_id: str
    target_bbox: PageBBox
    start_s: float
    stop_s: float


class VoiceRenderRecord(BaseModel):
    project_id: str
    status: Literal["voice_ready"] = "voice_ready"
    created_at: datetime
    mode: Literal["gradium", "mock"]
    voice_id: str | None = None
    audio_path: str
    duration_s: float
    text_segments: list[TimedText]
    narration_word_timings: list[TimedNarrationWord] = Field(default_factory=list)
    action_timings: list[TimedAction]
    timed_animation_beats: list[TimedAnimationBeat] = Field(default_factory=list)
    timed_transitions: list[TimedTransition] = Field(default_factory=list)
    caption_timeline: list[TimedText]
    warnings: list[str] = Field(default_factory=list)


class CreativeBrief(BaseModel):
    style: str = "clean academic explainer"
    voice_profile: str = "clear educational narrator"


class AnalysisSummary(BaseModel):
    analysis_path: str
    docling_path: str
    sections_path: str
    word_index_path: str
    page_image_dir: str
    page_count: int
    section_count: int
    narrated_unit_count: int
    visual_target_count: int
    action_count: int
    page_limit: int | None = None


class VoiceSummary(BaseModel):
    voice_path: str
    timeline_path: str
    caption_timeline_path: str
    segment_count: int
    word_timing_count: int = 0
    action_timing_count: int
    duration_s: float
    mode: Literal["gradium", "mock"]
    voice_id: str | None = None


class ProjectPageVideoAsset(BaseModel):
    status: Literal["idle", "queued", "generating", "ready", "failed"] = "idle"
    workflow_json_path: str | None = None
    overlay_image_path: str | None = None
    scene_data_path: str | None = None
    audio_path: str | None = None
    video_path: str | None = None
    summary_path: str | None = None
    error_message: str | None = None
    updated_at: str | None = None


class ProjectPageAsset(BaseModel):
    page: int
    width: float
    height: float
    image_path: str
    video: ProjectPageVideoAsset | None = None


class ProjectPageVideoResponse(BaseModel):
    status: Literal["idle", "queued", "generating", "ready", "failed"]
    overlay_image_url: str | None = None
    thumbnail_url: str | None = None
    scene_data_url: str | None = None
    audio_url: str | None = None
    video_url: str | None = None
    error_message: str | None = None
    updated_at: str | None = None


class ProjectPageResponse(BaseModel):
    page: int
    width: float
    height: float
    image_url: str
    video: ProjectPageVideoResponse | None = None


class ProjectRecord(BaseModel):
    project_id: str
    created_at: str
    source_filename: str
    pdf_storage_path: str
    creative_brief: CreativeBrief
    current_stage: Stage
    progress_percent: int
    stage_label: str
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    pages: list[ProjectPageAsset] = Field(default_factory=list)
    analysis: AnalysisSummary | None = None
    voice: VoiceSummary | None = None


class ProjectResponse(BaseModel):
    project_id: str
    created_at: str
    source_filename: str
    creative_brief: CreativeBrief
    current_stage: Stage
    progress_percent: int
    stage_label: str
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    pages: list[ProjectPageResponse] = Field(default_factory=list)
    analysis: AnalysisSummary | None = None
    voice: VoiceSummary | None = None


class AnalysisRequest(BaseModel):
    page_limit: int | None = Field(default=None, ge=1)
    section_limit: int | None = Field(default=None, ge=1)
    max_targets_per_section: int | None = Field(default=None, ge=1, le=8)
    use_mock_planner: bool = False


class RenderVoiceRequest(BaseModel):
    voice_id: str | None = None
    use_mock_voice: bool = False
    pause_between_sections_s: float = Field(default=0.4, ge=0.0, le=5.0)


class GeneratePageVideoRequest(BaseModel):
    voice_id: str | None = None
    use_mock_voice: bool = False
    max_sections: int = Field(default=2, ge=1, le=8)
    max_highlights: int = Field(default=4, ge=1, le=8)
    max_candidates: int = Field(default=180, ge=20, le=500)
    fps: int = Field(default=24, ge=12, le=60)


def _page_image_url(project_id: str, page: int) -> str:
    return f"/projects/{project_id}/pages/{page}/image"


def _page_overlay_url(project_id: str, page: int) -> str:
    return f"/projects/{project_id}/pages/{page}/overlay"


def _page_video_url(project_id: str, page: int) -> str:
    return f"/projects/{project_id}/pages/{page}/video"


def _page_scene_data_url(project_id: str, page: int) -> str:
    return f"/projects/{project_id}/pages/{page}/scene-data"


def _page_audio_url(project_id: str, page: int) -> str:
    return f"/projects/{project_id}/pages/{page}/audio"


def _page_response(project_id: str, page: ProjectPageAsset) -> ProjectPageResponse:
    video_response: ProjectPageVideoResponse | None = None
    if page.video is not None:
        overlay_image_url = (
            _page_overlay_url(project_id, page.page)
            if page.video.overlay_image_path
            else None
        )
        video_url = (
            _page_video_url(project_id, page.page) if page.video.video_path else None
        )
        audio_url = (
            _page_audio_url(project_id, page.page) if page.video.audio_path else None
        )
        scene_data_url = (
            _page_scene_data_url(project_id, page.page)
            if page.video.scene_data_path
            else None
        )
        video_response = ProjectPageVideoResponse(
            status=page.video.status,
            overlay_image_url=overlay_image_url,
            thumbnail_url=overlay_image_url or _page_image_url(project_id, page.page),
            scene_data_url=scene_data_url,
            audio_url=audio_url,
            video_url=video_url,
            error_message=page.video.error_message,
            updated_at=page.video.updated_at,
        )
    return ProjectPageResponse(
        page=page.page,
        width=page.width,
        height=page.height,
        image_url=_page_image_url(project_id, page.page),
        video=video_response,
    )


def project_response(project: ProjectRecord) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        created_at=project.created_at,
        source_filename=project.source_filename,
        creative_brief=project.creative_brief,
        current_stage=project.current_stage,
        progress_percent=project.progress_percent,
        stage_label=project.stage_label,
        error_message=project.error_message,
        warnings=project.warnings,
        pages=[_page_response(project.project_id, page) for page in project.pages],
        analysis=project.analysis,
        voice=project.voice,
    )

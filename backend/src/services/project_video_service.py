from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.models import GeneratePageVideoRequest, ProjectPageVideoAsset
from src.services.page_video_service import render_page_video
from src.storage import load_project, mutate_project, project_dir


def _set_failure(project_id: str, page: int, stage_label: str, exc: Exception) -> None:
    now = datetime.now(timezone.utc).isoformat()

    def _mutate(project) -> None:
        project.current_stage = "failed"
        project.progress_percent = 100
        project.stage_label = stage_label
        project.error_message = str(exc)
        for project_page in project.pages:
            if project_page.page != page:
                continue
            video = project_page.video or ProjectPageVideoAsset()
            video.status = "failed"
            video.error_message = str(exc)
            video.updated_at = now
            project_page.video = video

    mutate_project(project_id, _mutate)


def generate_project_page_video(
    project_id: str,
    page: int,
    request: GeneratePageVideoRequest,
) -> None:
    try:
        now = datetime.now(timezone.utc).isoformat()
        mutate_project(
            project_id,
            lambda project: (
                setattr(project, "current_stage", "planning_sections"),
                setattr(project, "progress_percent", 8),
                setattr(project, "stage_label", f"Generating workflow for page {page}"),
                setattr(project, "error_message", None),
                [
                    (
                        setattr(
                            project_page,
                            "video",
                            (
                                project_page.video or ProjectPageVideoAsset()
                            ).model_copy(
                                update={
                                    "status": "queued",
                                    "error_message": None,
                                    "updated_at": now,
                                }
                            ),
                        )
                    )
                    for project_page in project.pages
                    if project_page.page == page
                ],
            ),
        )

        project = load_project(project_id)
        selected_page = next((project_page for project_page in project.pages if project_page.page == page), None)
        if selected_page is None:
            raise RuntimeError(f"Unknown project page {page}.")

        mutate_project(
            project_id,
            lambda project_record: (
                setattr(project_record, "current_stage", "planning_sections"),
                setattr(project_record, "progress_percent", 28),
                setattr(project_record, "stage_label", f"Planning page {page} visuals"),
                [
                    setattr(project_page.video, "status", "generating")
                    for project_page in project_record.pages
                    if project_page.page == page and project_page.video is not None
                ],
            ),
        )

        render_dir = project_dir(project_id) / "page_videos" / f"page-{page:04d}"
        summary = render_page_video(
            pdf_path=Path(project.pdf_storage_path),
            page=page,
            output_dir=render_dir,
            max_sections=request.max_sections,
            max_highlights=request.max_highlights,
            max_candidates=request.max_candidates,
            fps=request.fps,
            voice_id=request.voice_id,
            use_mock_voice=request.use_mock_voice,
        )
        complete_time = datetime.now(timezone.utc).isoformat()

        mutate_project(
            project_id,
            lambda project_record: (
                setattr(project_record, "current_stage", "video_ready"),
                setattr(project_record, "progress_percent", 100),
                setattr(project_record, "stage_label", f"Video ready for page {page}"),
                setattr(project_record, "error_message", None),
                [
                    setattr(
                        project_page,
                        "video",
                        ProjectPageVideoAsset(
                            status="ready",
                            workflow_json_path=summary["workflow_json_path"],
                            overlay_image_path=summary.get("overlay_image_path"),
                            scene_data_path=summary["scene_data_path"],
                            audio_path=summary["final_audio_path"],
                            video_path=summary["final_video_path"],
                            summary_path=str((render_dir / "render" / "workflow_canvas_video_summary.json").resolve()),
                            error_message=None,
                            updated_at=complete_time,
                        ),
                    )
                    for project_page in project_record.pages
                    if project_page.page == page
                ],
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _set_failure(project_id, page, f"Video generation failed for page {page}", exc)

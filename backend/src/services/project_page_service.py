from __future__ import annotations

from pathlib import Path

from src.config import get_settings
from src.models import ProjectPageAsset
from src.services.pdf_service import render_pages
from src.storage import load_project, mutate_project, project_dir


def _set_failure(project_id: str, stage_label: str, exc: Exception) -> None:
    mutate_project(
        project_id,
        lambda project: (
            setattr(project, "current_stage", "failed"),
            setattr(project, "progress_percent", 100),
            setattr(project, "stage_label", stage_label),
            setattr(project, "error_message", str(exc)),
        ),
    )


def prepare_project_pages(project_id: str) -> None:
    try:
        mutate_project(
            project_id,
            lambda project: (
                setattr(project, "current_stage", "extracting_document"),
                setattr(project, "progress_percent", 10),
                setattr(project, "stage_label", "Rendering page thumbnails"),
                setattr(project, "error_message", None),
            ),
        )

        settings = get_settings()
        project = load_project(project_id)
        pdf_path = Path(project.pdf_storage_path)
        artifact_dir = project_dir(project_id)
        pages = render_pages(
            pdf_path,
            artifact_dir / "pages",
            settings.default_page_image_dpi,
        )
        page_assets = [
            ProjectPageAsset(
                page=page.page,
                width=page.width,
                height=page.height,
                image_path=page.image_path,
            )
            for page in pages
        ]

        mutate_project(
            project_id,
            lambda current: (
                setattr(current, "current_stage", "pages_ready"),
                setattr(current, "progress_percent", 100),
                setattr(current, "stage_label", "Pages ready"),
                setattr(current, "pages", page_assets),
                setattr(current, "error_message", None),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _set_failure(project_id, "Page preparation failed", exc)

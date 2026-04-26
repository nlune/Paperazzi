from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.models import (
    AnalysisRequest,
    CreativeBrief,
    ProjectRecord,
    ProjectResponse,
    RenderVoiceRequest,
    project_response,
)
from src.runtime import run_background_job
from src.services.analysis_service import analyze_project
from src.services.voice_service import render_voice
from src.storage import (
    atomic_write_bytes,
    load_project,
    mutate_project,
    project_dir,
    save_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _is_pdf(upload: UploadFile) -> bool:
    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _require_non_empty(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{label} is required.")
    return cleaned


def _ensure_project(project_id: str) -> ProjectRecord:
    try:
        return load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", response_model=ProjectResponse)
async def create_project(
    file: UploadFile = File(...),
    style: str = Form("clean academic explainer"),
    voice_profile: str = Form("clear educational narrator"),
) -> ProjectResponse:
    if not _is_pdf(file):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF.")

    style_value = _require_non_empty(style, "style")
    voice_value = _require_non_empty(voice_profile, "voice_profile")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")

    project_id = uuid4().hex
    folder = project_dir(project_id)
    pdf_path = folder / "source.pdf"

    try:
        folder.mkdir(parents=True, exist_ok=False)
        atomic_write_bytes(pdf_path, file_bytes)
    finally:
        await file.close()

    project = ProjectRecord(
        project_id=project_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_filename=file.filename or "uploaded.pdf",
        pdf_storage_path=str(pdf_path.resolve()),
        creative_brief=CreativeBrief(style=style_value, voice_profile=voice_value),
        current_stage="created",
        progress_percent=0,
        stage_label="Project created",
    )
    save_project(project)
    return project_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str) -> ProjectResponse:
    return project_response(_ensure_project(project_id))


@router.get("/{project_id}/analysis")
async def get_analysis(project_id: str) -> dict:
    project = _ensure_project(project_id)
    if project.analysis is None:
        raise HTTPException(status_code=404, detail="Analysis artifact not available.")
    return json.loads(Path(project.analysis.analysis_path).read_text(encoding="utf-8"))


@router.get("/{project_id}/voice")
async def get_voice(project_id: str) -> dict:
    project = _ensure_project(project_id)
    if project.voice is None:
        raise HTTPException(status_code=404, detail="Voice artifact not available.")
    voice_render_path = project_dir(project_id) / "voice_render.json"
    if not voice_render_path.exists():
        raise HTTPException(status_code=404, detail="Voice render artifact missing.")
    return json.loads(voice_render_path.read_text(encoding="utf-8"))


@router.post("/{project_id}/analysis", response_model=ProjectResponse)
async def start_analysis(
    project_id: str,
    request: AnalysisRequest,
) -> ProjectResponse:
    project = _ensure_project(project_id)
    if project.current_stage in {"extracting_document", "planning_sections", "generating_voice"}:
        raise HTTPException(status_code=409, detail="Another project job is already running.")

    updated = mutate_project(
        project_id,
        lambda current: (
            setattr(current, "current_stage", "extracting_document"),
            setattr(current, "progress_percent", 2),
            setattr(current, "stage_label", "Queued analysis"),
            setattr(current, "error_message", None),
            setattr(current, "analysis", None),
            setattr(current, "voice", None),
        ),
    )
    run_background_job(analyze_project, project_id, request)
    return project_response(updated)


@router.post("/{project_id}/render_voice", response_model=ProjectResponse)
async def start_render_voice(
    project_id: str,
    request: RenderVoiceRequest,
) -> ProjectResponse:
    project = _ensure_project(project_id)
    if project.current_stage in {"extracting_document", "planning_sections", "generating_voice"}:
        raise HTTPException(status_code=409, detail="Another project job is already running.")
    if project.analysis is None:
        raise HTTPException(status_code=409, detail="Analysis must exist before render_voice.")

    updated = mutate_project(
        project_id,
        lambda current: (
            setattr(current, "current_stage", "generating_voice"),
            setattr(current, "progress_percent", 2),
            setattr(current, "stage_label", "Queued voice generation"),
            setattr(current, "error_message", None),
            setattr(current, "voice", None),
        ),
    )
    run_background_job(render_voice, project_id, request)
    return project_response(updated)

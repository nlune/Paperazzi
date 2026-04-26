from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    gemini_model: str
    gradium_base_url: str
    gradium_voice_id: str | None
    projects_dir: Path
    revideo_dir: Path
    allow_mock_services: bool
    default_page_image_dpi: int
    default_section_limit: int
    default_max_targets: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root_dir = Path(__file__).resolve().parent.parent
    projects_dir = Path(
        os.getenv("PAPERAZZI_PROJECTS_DIR", root_dir / "storage" / "projects")
    )
    projects_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"),
        gradium_base_url=os.getenv(
            "GRADIUM_BASE_URL", "https://eu.api.gradium.ai/api/"
        ),
        gradium_voice_id=os.getenv("GRADIUM_VOICE_ID"),
        projects_dir=projects_dir,
        revideo_dir=Path(
            os.getenv("PAPERAZZI_REVIDEO_DIR", root_dir.parent / "revideo")
        ),
        allow_mock_services=_env_flag("PAPERAZZI_ALLOW_MOCK_SERVICES", True),
        default_page_image_dpi=int(os.getenv("PAPERAZZI_PAGE_IMAGE_DPI", "144")),
        default_section_limit=int(os.getenv("PAPERAZZI_SECTION_LIMIT", "12")),
        default_max_targets=int(os.getenv("PAPERAZZI_MAX_TARGETS", "3")),
    )

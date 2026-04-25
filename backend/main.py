import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

from fastapi import FastAPI, File, HTTPException, UploadFile
from pypdf import PdfReader, PdfWriter
from fastapi.middleware.cors import CORSMiddleware
from google import genai

app = FastAPI(title="Paperazzi Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_PAGE_LIMIT = 1000
PROMPT = """
Analyze the attached PDF and identify the top 5 concepts.

Return only a JSON object with exactly 5 key/value pairs.
- Each key must be the concept name.
- Each value must be a short explanation string.
- Do not include markdown, code fences, numbering, or extra text.
""".strip()


@lru_cache(maxsize=1)
def get_genai_client() -> genai.Client:
    return genai.Client()


def _truncate_pdf_if_needed(src_path: str) -> str:
    """Return src_path unchanged, or write a truncated copy and return its path."""
    reader = PdfReader(src_path)
    if len(reader.pages) <= GEMINI_PAGE_LIMIT:
        return src_path
    writer = PdfWriter()
    for page in reader.pages[:GEMINI_PAGE_LIMIT]:
        writer.add_page(page)
    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        writer.write(tmp)
        return tmp.name


def _is_pdf(upload: UploadFile) -> bool:
    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _extract_json_object(text: str) -> dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("Gemini did not return valid JSON.")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError("Gemini returned malformed JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Gemini response must be a JSON object.")

    normalized: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Gemini returned an invalid concept name.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Gemini returned an invalid concept explanation.")
        normalized[key.strip()] = value.strip()

    if len(normalized) != 5:
        raise ValueError("Gemini must return exactly 5 concepts.")

    return normalized


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/concepts")
async def extract_concepts(file: UploadFile = File(...)) -> dict[str, str]:
    if not _is_pdf(file):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")

    temp_path: str | None = None
    upload_path: str | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(file_bytes)
            temp_path = temp_file.name

        upload_path = _truncate_pdf_if_needed(temp_path)

        uploaded_pdf = get_genai_client().files.upload(file=upload_path)
        response = get_genai_client().models.generate_content(
            model=MODEL_NAME,
            contents=[uploaded_pdf, PROMPT],
            config={"response_mime_type": "application/json"},
        )

        if not response.text or not response.text.strip():
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty response.",
            )

        try:
            return _extract_json_object(response.text)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Unexpected error: {str(exc)}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to extract concepts from Gemini: {exc}",
        ) from exc
    finally:
        await file.close()
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
        if upload_path and upload_path != temp_path:
            Path(upload_path).unlink(missing_ok=True)

# Paperazzi

Paperazzi turns dense research papers into short, visual explainers.  
Upload a PDF, select a page, and generate a narrated video that highlights key concepts with guided motion and callouts.

## Demo

▶️ [Demo video](https://youtu.be/feYS0lDS7lk)

## What It Does

- Ingests a PDF and builds a project workspace.
- Renders page thumbnails for quick browsing.
- Plans page-level narration and highlight actions with Gemini.
- Produces scene data, voice audio, and final MP4 output.
- Serves all assets through a simple frontend-friendly API contract.

## Product Flow

1. Upload PDF in the frontend.
2. Backend creates a project and prepares pages.
3. Pick a page and trigger `generate_video`.
4. Backend runs workflow planning, timing, and rendering.
5. Watch the generated MP4 in the same workspace.

## Architecture Snapshot

- **Frontend:** Next.js app (upload, polling, video workspace UI)
- **Backend:** FastAPI services (project lifecycle, workflow generation, rendering)
- **Storage:** Filesystem artifacts per project (`project.json`, workflow JSON, scene data, audio, video)

<p align="center">
  <img width="920" alt="Paperazzi preview" src="https://github.com/user-attachments/assets/a46d64d1-2d3c-4b47-8ce7-91a106368272" />
</p>

## Repo Structure

- `app/` - frontend routes and UI
- `lib/` - frontend API client and shared helpers
- `backend/` - FastAPI app, pipeline services, tests, and run docs
- `revideo/`, `motion-canvas/` - rendering-related experiments/assets

## Quick Start

See `backend/RUN.md` for backend setup and end-to-end frontend testing flow.

"use client";

export type ProjectPageVideo = {
  status: "idle" | "queued" | "generating" | "ready" | "failed";
  overlay_image_url: string | null;
  thumbnail_url: string | null;
  scene_data_url: string | null;
  audio_url: string | null;
  video_url: string | null;
  error_message: string | null;
  updated_at: string | null;
};

export type ProjectPage = {
  page: number;
  width: number;
  height: number;
  image_url: string;
  video: ProjectPageVideo | null;
};

export type ProjectResponse = {
  project_id: string;
  created_at: string;
  source_filename: string;
  current_stage: string;
  progress_percent: number;
  stage_label: string;
  error_message: string | null;
  warnings: string[];
  pages: ProjectPage[];
};

export type GeneratePageVideoRequest = {
  voice_id?: string | null;
  use_mock_voice?: boolean;
  max_sections?: number;
  max_highlights?: number;
  max_candidates?: number;
  fps?: number;
};

const API_BASE = process.env.NEXT_PUBLIC_PAPERAZZI_API_BASE_URL ?? "http://localhost:8000";

export function assetUrl(path: string | null | undefined): string | null {
  if (!path) {
    return null;
  }
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${API_BASE}${path}`;
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function createProject(file: File): Promise<ProjectResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE}/projects`, {
    method: "POST",
    body: formData,
  });
  return parseJson<ProjectResponse>(response);
}

export async function fetchProject(projectId: string): Promise<ProjectResponse> {
  const response = await fetch(`${API_BASE}/projects/${projectId}`, {
    cache: "no-store",
  });
  return parseJson<ProjectResponse>(response);
}

export async function generatePageVideo(
  projectId: string,
  page: number,
  body: GeneratePageVideoRequest = {},
): Promise<ProjectResponse> {
  const response = await fetch(`${API_BASE}/projects/${projectId}/pages/${page}/generate_video`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson<ProjectResponse>(response);
}

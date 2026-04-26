"use client";

import { useEffect, useMemo, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { LoaderCircle, Play, Sparkles, Video } from "lucide-react";

import { PaperazziLogo } from "@/components/PaperazziLogo";
import { Progress } from "@/components/ui/progress";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  assetUrl,
  fetchProject,
  generatePageVideo,
  type ProjectPage,
  type ProjectResponse,
} from "@/lib/paperazzi-api";
import { toast } from "sonner";

const RUNNING_STAGES = new Set([
  "extracting_document",
  "planning_sections",
  "generating_voice",
  "rendering_video",
]);

function statusLabel(page: ProjectPage | null): string {
  if (!page?.video) {
    return "Ready to generate";
  }
  switch (page.video.status) {
    case "queued":
      return "Queued";
    case "generating":
      return "Generating";
    case "ready":
      return "Video ready";
    case "failed":
      return "Generation failed";
    default:
      return "Ready to generate";
  }
}

export default function VideoWorkspacePage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = searchParams.get("projectId");

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [selectedPageNumber, setSelectedPageNumber] = useState<number | null>(null);
  const [playerOpen, setPlayerOpen] = useState(false);
  const [isTriggering, setIsTriggering] = useState(false);

  useEffect(() => {
    if (!projectId) {
      router.push("/");
      return;
    }

    let cancelled = false;
    let intervalId: number | null = null;

    const loadProject = async () => {
      try {
        const nextProject = await fetchProject(projectId);
        if (cancelled) {
          return;
        }
        setProject(nextProject);
        setSelectedPageNumber((current) => {
          if (current && nextProject.pages.some((page) => page.page === current)) {
            return current;
          }
          return nextProject.pages[0]?.page ?? null;
        });
      } catch (error) {
        if (!cancelled) {
          console.error(error);
          toast.error(error instanceof Error ? error.message : "Failed to load project.");
        }
      }
    };

    void loadProject();
    intervalId = window.setInterval(() => {
      void loadProject();
    }, 1500);

    return () => {
      cancelled = true;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, [projectId, router]);

  const selectedPage = useMemo(
    () => project?.pages.find((page) => page.page === selectedPageNumber) ?? null,
    [project?.pages, selectedPageNumber],
  );

  const runningJob = Boolean(project && RUNNING_STAGES.has(project.current_stage));
  const selectedVideoUrl = assetUrl(selectedPage?.video?.video_url);
  const selectedThumbnailUrl = assetUrl(
    selectedPage?.video?.thumbnail_url ?? selectedPage?.image_url,
  );

  const handleSelectPage = async (page: ProjectPage) => {
    setSelectedPageNumber(page.page);
    if (!projectId) {
      return;
    }
    if (page.video?.status === "ready" || page.video?.status === "generating" || page.video?.status === "queued") {
      return;
    }
    if (runningJob || isTriggering) {
      return;
    }

    try {
      setIsTriggering(true);
      const nextProject = await generatePageVideo(projectId, page.page, {});
      setProject(nextProject);
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : "Failed to start video generation.");
    } finally {
      setIsTriggering(false);
    }
  };

  return (
    <>
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 15% 20%, color-mix(in oklab, var(--sunset) 22%, transparent), transparent 32%), radial-gradient(circle at 85% 18%, color-mix(in oklab, var(--teal) 18%, transparent), transparent 28%), linear-gradient(180deg, rgba(255,255,255,0.03), transparent 42%)",
        }}
      />
      <header className="relative z-10 mx-auto flex max-w-7xl items-center justify-between px-6 py-6">
        <Link href="/">
          <PaperazziLogo />
        </Link>
        <div className="rounded-full border border-border bg-card/70 px-4 py-2 text-xs text-muted-foreground backdrop-blur">
          {project?.source_filename ?? "Workspace"}
        </div>
      </header>

      <main className="relative z-10 mx-auto flex max-w-7xl gap-6 px-6 pb-16">
        <aside className="w-[320px] shrink-0 rounded-[28px] border border-border bg-card/75 p-4 backdrop-blur">
          <div className="rounded-2xl border border-border bg-background/60 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                  Project Status
                </p>
                <h2 className="mt-1 text-lg font-semibold">
                  {project?.stage_label ?? "Loading project"}
                </h2>
              </div>
              {(runningJob || isTriggering) && (
                <LoaderCircle className="h-5 w-5 animate-spin text-[var(--sunset)]" />
              )}
            </div>
            <Progress className="mt-4 h-2.5 bg-secondary" value={project?.progress_percent ?? 0} />
            <p className="mt-3 text-sm text-muted-foreground">
              {project?.error_message ??
                "Upload a paper, choose a page, and we will build a zoomed explainer around that page."}
            </p>
          </div>

          <div className="mt-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                Pages
              </h3>
              <span className="text-xs text-muted-foreground">
                {project?.pages.length ?? 0} total
              </span>
            </div>
            <div className="max-h-[calc(100vh-260px)] space-y-3 overflow-y-auto pr-1">
              {project?.pages.map((page) => {
                const active = selectedPageNumber === page.page;
                const thumbUrl = assetUrl(page.image_url) ?? "";
                return (
                  <button
                    key={page.page}
                    type="button"
                    onClick={() => void handleSelectPage(page)}
                    disabled={runningJob && selectedPageNumber !== page.page}
                    className={`w-full rounded-2xl border p-2 text-left transition-all ${
                      active
                        ? "border-[var(--sunset)] bg-background shadow-[0_0_0_1px_color-mix(in_oklab,var(--sunset)_45%,transparent)]"
                        : "border-border bg-background/55 hover:border-[var(--amber)]"
                    } disabled:cursor-not-allowed disabled:opacity-60`}
                  >
                    <div className="relative overflow-hidden rounded-xl border border-border bg-secondary">
                      <div className="relative aspect-[3/4]">
                        <Image
                          src={thumbUrl}
                          alt={`Page ${page.page}`}
                          fill
                          className="object-cover"
                          unoptimized
                        />
                      </div>
                      {page.video?.status === "ready" && (
                        <div className="absolute right-2 top-2 rounded-full bg-black/70 px-2 py-1 text-[10px] font-semibold text-white">
                          VIDEO
                        </div>
                      )}
                    </div>
                    <div className="px-1 pb-1 pt-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">Page {page.page}</span>
                        <span className="rounded-full border border-border px-2 py-1 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                          {statusLabel(page)}
                        </span>
                      </div>
                    </div>
                  </button>
                );
              })}
              {!project && (
                <div className="rounded-2xl border border-border bg-background/55 p-4 text-sm text-muted-foreground">
                  Loading page thumbnails…
                </div>
              )}
            </div>
          </div>
        </aside>

        <section className="min-w-0 flex-1 rounded-[32px] border border-border bg-card/80 p-6 backdrop-blur">
          <div className="flex items-start justify-between gap-6">
            <div>
              <p className="text-xs uppercase tracking-[0.2em] text-[var(--teal)]">
                Canvas Workflow
              </p>
              <h1 className="mt-2 text-4xl font-semibold tracking-tight md:text-5xl">
                {selectedPage ? `Page ${selectedPage.page}` : "Select a page"}
              </h1>
              <p className="mt-3 max-w-2xl text-sm text-muted-foreground md:text-base">
                Click a page thumbnail to generate a focused explainer. The backend will plan the page, synthesize narration, and render the final MP4.
              </p>
            </div>
            {selectedPage && selectedPage.video?.status === "ready" && (
              <button
                type="button"
                onClick={() => setPlayerOpen(true)}
                className="rounded-full border border-border bg-background px-4 py-2 text-sm font-semibold transition-colors hover:bg-secondary"
              >
                Watch video
              </button>
            )}
          </div>

          <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
            <div className="overflow-hidden rounded-[28px] border border-border bg-background">
              {selectedPage ? (
                <div className="relative aspect-video bg-black">
                  {selectedThumbnailUrl ? (
                    <Image
                      src={selectedThumbnailUrl}
                      alt={`Page ${selectedPage.page}`}
                      fill
                      className="object-cover"
                      unoptimized
                    />
                  ) : null}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent" />
                  {selectedPage.video?.status === "ready" && selectedVideoUrl ? (
                    <button
                      type="button"
                      onClick={() => setPlayerOpen(true)}
                      className="absolute inset-0 flex items-center justify-center"
                    >
                      <span className="flex h-24 w-24 items-center justify-center rounded-full bg-white/88 text-black shadow-2xl transition-transform hover:scale-105">
                        <Play className="ml-1 h-10 w-10 fill-current" />
                      </span>
                    </button>
                  ) : (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center text-white">
                      {(runningJob || isTriggering) && selectedPageNumber === selectedPage.page ? (
                        <>
                          <LoaderCircle className="h-10 w-10 animate-spin" />
                          <p className="text-lg font-semibold">{project?.stage_label ?? "Generating video"}</p>
                          <p className="max-w-md text-sm text-white/70">
                            We are building the workflow, synthesizing audio, and rendering the final video now.
                          </p>
                        </>
                      ) : (
                        <>
                          <Sparkles className="h-10 w-10" />
                          <p className="text-lg font-semibold">Click this page on the left to generate its video</p>
                        </>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex aspect-video items-center justify-center bg-secondary text-muted-foreground">
                  No page selected.
                </div>
              )}
            </div>

            <div className="space-y-4">
              <div className="rounded-2xl border border-border bg-background/70 p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                  Selected Page
                </p>
                <p className="mt-2 text-2xl font-semibold">
                  {selectedPage ? `Page ${selectedPage.page}` : "None"}
                </p>
                <p className="mt-2 text-sm text-muted-foreground">
                  {selectedPage
                    ? statusLabel(selectedPage)
                    : "Choose a page from the left rail to start."}
                </p>
              </div>

              <div className="rounded-2xl border border-border bg-background/70 p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                  Output
                </p>
                <div className="mt-3 flex items-start gap-3">
                  <div className="rounded-2xl bg-secondary p-3 text-[var(--sunset)]">
                    <Video className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="font-medium">Canvas MP4 render</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Generated videos stay attached to each page tile and can be reopened anytime from this workspace.
                    </p>
                  </div>
                </div>
                {selectedPage?.video?.error_message ? (
                  <p className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                    {selectedPage.video.error_message}
                  </p>
                ) : null}
              </div>
            </div>
          </div>
        </section>
      </main>

      <Dialog open={playerOpen} onOpenChange={setPlayerOpen}>
        <DialogContent className="max-w-5xl border-border bg-background/98 p-3 sm:rounded-3xl">
          <DialogHeader className="px-3 pt-2">
            <DialogTitle>{selectedPage ? `Page ${selectedPage.page} video` : "Generated video"}</DialogTitle>
            <DialogDescription>
              {project?.source_filename ?? "Paperazzi render"}
            </DialogDescription>
          </DialogHeader>
          <div className="overflow-hidden rounded-2xl border border-border bg-black">
            {selectedVideoUrl ? (
              <video
                key={selectedVideoUrl}
                className="aspect-video w-full"
                src={selectedVideoUrl}
                controls
                playsInline
                autoPlay
              />
            ) : (
              <div className="flex aspect-video items-center justify-center text-sm text-white/70">
                Video not available yet.
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

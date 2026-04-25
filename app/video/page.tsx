"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { PaperazziLogo } from "@/components/PaperazziLogo";
import { StepIndicator } from "@/components/StepIndicator";
import { Confetti } from "@/components/Confetti";
import { LoadingOverlay } from "@/components/LoadingOverlay";
import { usePaperazziStore } from "@/lib/paperazzi-store";
import { STAGES } from "@/lib/constants";

export default function VideoPage() {
  const router = useRouter();
  const claim = usePaperazziStore((s) => s.getSelectedClaim());
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState(0);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!claim) {
      router.push("/");
    }
  }, [claim, router]);

  useEffect(() => {
    if (ready) return;
    const t = setInterval(() => {
      setProgress((p) => {
        const next = Math.min(100, p + Math.random() * 6 + 2);
        const s = Math.min(STAGES.length - 1, Math.floor((next / 100) * STAGES.length));
        setStage(s);
        if (next >= 100) {
          clearInterval(t);
          setTimeout(() => setReady(true), 400);
        }
        return next;
      });
    }, 350);
    return () => clearInterval(t);
  }, [ready]);

  return (
    <>
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Link href="/">
          <PaperazziLogo />
        </Link>
        <span className="text-xs text-muted-foreground">Step 3 of 3</span>
      </header>

      <main className="mx-auto max-w-4xl px-6 pb-24 pt-6">
        <div className="flex justify-center">
          <StepIndicator current={3} />
        </div>

        <div className="mt-10 text-center">
          <p className="text-xs uppercase tracking-[0.2em] text-[var(--teal)]">
            {claim?.section ?? "Your video"}
          </p>
          <h1 className="mt-3 text-balance text-4xl font-semibold tracking-tight md:text-5xl">
            {claim?.title ?? "Your video"}
          </h1>
        </div>

        {/* Player */}
        <div
          className="relative mt-10 aspect-video overflow-hidden rounded-3xl border border-border bg-black"
          style={{ boxShadow: "var(--shadow-card)" }}
        >
          {/* Decorative gradient backdrop while not playing */}
          <div
            className={`absolute inset-0 transition-opacity duration-500 ${playing ? "opacity-0" : "opacity-100"}`}
            style={{
              background:
                "radial-gradient(ellipse at 30% 30%, color-mix(in oklab, var(--sunset) 55%, transparent), transparent 60%), radial-gradient(ellipse at 70% 70%, color-mix(in oklab, var(--teal) 45%, transparent), transparent 60%), oklch(0.10 0.005 60)",
            }}
          />

          {ready ? (
            <>
              <video
                ref={videoRef}
                className="absolute inset-0 h-full w-full object-cover"
                playsInline
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                poster=""
              >
                {/* Placeholder — would be the generated mp4 */}
              </video>

              {!playing && (
                <button
                  onClick={() => {
                    setPlaying(true);
                    videoRef.current?.play().catch(() => setPlaying(true));
                  }}
                  className="absolute inset-0 flex items-center justify-center"
                  aria-label="Play video"
                >
                  <div
                    className="flex h-20 w-20 items-center justify-center rounded-full transition-transform hover:scale-105"
                    style={{
                      background: "var(--gradient-sunset)",
                      boxShadow: "var(--shadow-glow)",
                    }}
                  >
                    <svg className="ml-1 h-8 w-8 text-background" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </div>
                </button>
              )}

              {/* Bottom info bar */}
              <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between bg-gradient-to-t from-black/80 to-transparent p-5">
                <div>
                  <p className="text-xs uppercase tracking-wider text-white/60">Now playing</p>
                  <p className="mt-0.5 text-sm font-medium text-white">{claim?.title}</p>
                </div>
                <span className="rounded-full bg-white/15 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur-md">
                  1080p
                </span>
              </div>
            </>
          ) : (
            <LoadingOverlay progress={progress} stage={STAGES[stage].label} emoji={STAGES[stage].emoji} />
          )}
        </div>
        {ready && <Confetti />}

        {/* Actions */}
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <button
            disabled={!ready}
            className="rounded-xl px-5 py-3 text-sm font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-40"
            style={{ background: "var(--gradient-sunset)", color: "var(--background)" }}
          >
            ↓ Download MP4
          </button>
          <button
            disabled={!ready}
            className="rounded-xl border border-border bg-card px-5 py-3 text-sm font-semibold transition-all hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-40"
          >
            Share link
          </button>
          <Link
            href="/claims"
            className="rounded-xl px-5 py-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            ← Pick another claim
          </Link>
        </div>
      </main>
    </>
  );
}

"use client";

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { PaperazziLogo } from "@/components/PaperazziLogo";
import { StepIndicator } from "@/components/StepIndicator";
import { ConfidencePill } from "@/components/ConfidencePill";
import { usePaperazziStore } from "@/lib/paperazzi-store";

export default function ClaimsPage() {
  const router = useRouter();
  const { claims, fileName, selectedClaimId, selectClaim } = usePaperazziStore();

  useEffect(() => {
    if (claims.length === 0) {
      router.push("/");
    }
  }, [claims, router]);

  const selected = useMemo(
    () => claims.find((c) => c.id === selectedClaimId) ?? null,
    [claims, selectedClaimId],
  );

  const handleGenerate = () => {
    if (!selectedClaimId) return;
    router.push("/video");
  };

  return (
    <>
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Link href="/">
          <PaperazziLogo />
        </Link>
        <span className="text-xs text-muted-foreground">Step 2 of 3</span>
      </header>

      <main className="mx-auto max-w-5xl px-6 pb-32 pt-6">
        <div className="flex justify-center">
          <StepIndicator current={2} />
        </div>

        <div className="mt-10 text-center">
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            {fileName ?? "Your paper"}
          </p>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight md:text-5xl">
            Pick the claim
            <br />
            <span
              className="bg-clip-text text-transparent"
              style={{ backgroundImage: "var(--gradient-sunset)" }}
            >
              worth a story.
            </span>
          </h1>
          <p className="mt-4 text-muted-foreground">
            We dug up <span className="font-semibold text-foreground">{claims.length}</span> juicy claims. Pick your favorite. 🎯
          </p>
        </div>

        <ul className="mt-10 space-y-3">
          {claims.map((c, i) => {
            const active = selectedClaimId === c.id;
            const emoji = ["🚀", "🏆", "⚡", "🛡️", "🧠"][i % 5];
            return (
              <li
                key={c.id}
                className="pop-in"
                style={{ animationDelay: `${i * 70}ms` }}
              >
                <button
                  onClick={() => selectClaim(c.id)}
                  className={`group w-full rounded-2xl border p-5 text-left transition-all duration-200 hover:-translate-y-0.5 ${
                    active
                      ? "border-[var(--sunset)] bg-card"
                      : "border-border bg-card/70 hover:border-[var(--amber)] hover:bg-card"
                  }`}
                  style={{
                    boxShadow: active ? "var(--shadow-glow)" : undefined,
                  }}
                >
                  <div className="flex items-start gap-4">
                    <div
                      className={`relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-xl transition-all group-hover:scale-110 group-hover:rotate-6 ${
                        active ? "text-background" : "bg-secondary"
                      }`}
                      style={active ? { background: "var(--gradient-sunset)" } : undefined}
                    >
                      <span>{emoji}</span>
                      <span
                        className={`absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full border-2 border-background text-[10px] font-bold ${
                          active ? "bg-background text-foreground" : "bg-[var(--teal)] text-background"
                        }`}
                      >
                        {i + 1}
                      </span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <h3 className="text-lg font-semibold leading-tight">{c.title}</h3>
                        <ConfidencePill v={c.confidence} />
                      </div>
                      <p className="mt-1.5 text-sm text-muted-foreground">{c.summary}</p>
                      <div className="mt-3 flex items-center gap-2">
                        <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] uppercase tracking-wider text-[var(--teal)]">
                          {c.section}
                        </span>
                        {c.confidence >= 0.9 && (
                          <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold text-background"
                                style={{ background: "var(--gradient-sunset)" }}>
                            🔥 Hot pick
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </main>

      {/* Sticky generate bar */}
      <div className="fixed inset-x-0 bottom-0 z-20">
        <div
          className="mx-auto max-w-3xl px-6 pb-6"
          style={{
            background: "linear-gradient(180deg, transparent, oklch(0.10 0.005 60) 40%)",
            paddingTop: "3rem",
          }}
        >
          <div className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-card p-3 pl-5"
               style={{ boxShadow: "var(--shadow-card)" }}>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">
                {selected ? selected.title : "No claim selected"}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {selected ? "Ready to generate a 1080p video" : "Tap a claim above"}
              </p>
            </div>
            <button
              onClick={handleGenerate}
              disabled={!selected}
              className="rounded-xl px-5 py-3 text-sm font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-40"
              style={{
                background: "var(--gradient-sunset)",
                color: "var(--background)",
                boxShadow: selected ? "var(--shadow-glow)" : undefined,
              }}
            >
              Generate video →
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { PaperazziLogo } from "@/components/PaperazziLogo";
import { StepIndicator } from "@/components/StepIndicator";
import { ConfidencePill } from "@/components/ConfidencePill";
import { usePaperazziStore } from "@/lib/paperazzi-store";
import { toast } from "sonner";

export default function ConceptsPage() {
  const router = useRouter();
  const { concepts, fileName, selectedConceptId, selectConcept } = usePaperazziStore();
  const [generatingImages, setGeneratingImages] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (concepts.length === 0) {
      router.push("/");
    }
  }, [concepts, router]);

  const selected = useMemo(
    () => concepts.find((c) => c.id === selectedConceptId) ?? null,
    [concepts, selectedConceptId],
  );

  const handleGenerateImage = async (conceptId: string, title: string) => {
    if (generatingImages.has(conceptId)) return;

    setGeneratingImages((prev) => new Set([...prev, conceptId]));
    try {
      const response = await fetch("/api/generate-image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: `${title} - scientific concept visualization, clean minimalist design, modern` }),
      });

      if (!response.ok) throw new Error("Failed to generate image");
      const { imageUrl } = await response.json();

      usePaperazziStore.setState((state) => ({
        concepts: state.concepts.map((c) =>
          c.id === conceptId ? { ...c, imageUrl } : c
        ),
      }));
    } catch (error) {
      console.error("Image generation error:", error);
      toast.error("Failed to generate image");
    } finally {
      setGeneratingImages((prev) => {
        const next = new Set(prev);
        next.delete(conceptId);
        return next;
      });
    }
  };

  const handleGenerateVideo = () => {
    if (!selectedConceptId) return;
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

      <main className="mx-auto max-w-7xl px-6 pb-32 pt-6">
        <div className="flex justify-center">
          <StepIndicator current={2} />
        </div>

        <div className="mt-10 text-center">
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            {fileName ?? "Your paper"}
          </p>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight md:text-5xl">
            Pick a concept
            <br />
            <span
              className="bg-clip-text text-transparent"
              style={{ backgroundImage: "var(--gradient-sunset)" }}
            >
              to visualize.
            </span>
          </h1>
          <p className="mt-4 text-muted-foreground">
            We found <span className="font-semibold text-foreground">{concepts.length}</span> brilliant concepts. Each one gets its own vibe. 🎨
          </p>
        </div>

        <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {concepts.map((concept, i) => {
            const active = selectedConceptId === concept.id;
            const isGenerating = generatingImages.has(concept.id);

            return (
              <button
                key={concept.id}
                onClick={() => selectConcept(concept.id)}
                className={`pop-in group relative overflow-hidden rounded-2xl border transition-all duration-200 hover:-translate-y-1 ${
                  active
                    ? "border-[var(--sunset)] shadow-lg"
                    : "border-border hover:border-[var(--amber)]"
                }`}
                style={{
                  animationDelay: `${i * 70}ms`,
                  boxShadow: active ? "var(--shadow-glow)" : undefined,
                }}
              >
                <div className="aspect-square overflow-hidden bg-secondary/30 relative">
                  {concept.imageUrl ? (
                    <Image
                      src={concept.imageUrl}
                      alt={concept.title}
                      fill
                      className="object-cover group-hover:scale-110 transition-transform duration-300"
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center">
                      {isGenerating ? (
                        <div className="flex flex-col items-center gap-2">
                          <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-[var(--sunset)]" />
                          <span className="text-xs text-muted-foreground">Generating...</span>
                        </div>
                      ) : (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleGenerateImage(concept.id, concept.title);
                          }}
                          className="rounded-lg bg-secondary px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary/80 transition-colors"
                        >
                          Generate Image
                        </button>
                      )}
                    </div>
                  )}
                </div>

                <div className="p-4">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-semibold leading-tight text-left">{concept.title}</h3>
                    <ConfidencePill v={concept.confidence} />
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground text-left line-clamp-2">
                    {concept.summary}
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--teal)]">
                      {concept.section}
                    </span>
                  </div>
                </div>

                {active && (
                  <div
                    className="absolute inset-0 pointer-events-none rounded-2xl border-2"
                    style={{ borderColor: "var(--sunset)" }}
                  />
                )}
              </button>
            );
          })}
        </div>
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
          <div
            className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-card p-3 pl-5"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">
                {selected ? selected.title : "No concept selected"}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {selected ? "Ready to generate a 1080p video" : "Pick a concept above"}
              </p>
            </div>
            <button
              onClick={handleGenerateVideo}
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

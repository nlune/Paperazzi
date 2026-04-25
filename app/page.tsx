"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { PaperazziLogo } from "@/components/PaperazziLogo";
import { StepIndicator } from "@/components/StepIndicator";
import { Sticker } from "@/components/Sticker";
import { usePaperazziStore } from "@/lib/paperazzi-store";
import { toast } from "sonner";

export default function UploadPage() {
  const router = useRouter();
  const { setFile, setConcepts, setLoading, isLoading } = usePaperazziStore();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        toast.error("Please upload a PDF file");
        return;
      }

      setFileName(file.name);
      setLoading(true);
      setFile(file.name);

      try {
        const formData = new FormData();
        formData.append("file", file);

        const response = await fetch("http://localhost:8000/concepts", {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          throw new Error("Failed to extract concepts");
        }

        const data = await response.json();

        // Transform backend dict { "Title": "Summary" } to Concept[]
        const concepts = Object.entries(data).map(([title, summary], index) => ({
          id: `c${index}`,
          title,
          summary: summary as string,
          section: "Core Concept",
          confidence: 0.95 - index * 0.05,
        }));

        setConcepts(concepts);
        router.push("/concepts");
      } catch (error) {
        console.error("Upload error:", error);
        toast.error("Failed to process PDF. Is the backend running?");
        setLoading(false);
      }
    },
    [router, setFile, setConcepts, setLoading],
  );

  return (
    <div className="relative overflow-hidden">
      {/* Floating blobs */}
      <div
        className="pointer-events-none absolute -left-24 top-24 h-72 w-72 opacity-30 blur-3xl blob"
        style={{ background: "var(--sunset)", animation: "blob 14s ease-in-out infinite, drift 18s ease-in-out infinite" }}
      />
      <div
        className="pointer-events-none absolute -right-20 top-72 h-80 w-80 opacity-25 blur-3xl blob"
        style={{ background: "var(--teal)", animation: "blob 16s ease-in-out infinite reverse, drift 22s ease-in-out infinite" }}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={{ background: "var(--gradient-hero)" }}
      />
      <header className="relative z-10 mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <PaperazziLogo />
        <span className="rounded-full border border-border bg-card/60 px-3 py-1 text-xs text-muted-foreground backdrop-blur">
          Step 1 of 3
        </span>
      </header>

      <main className="relative z-10 mx-auto flex max-w-3xl flex-col items-center px-6 pb-24 pt-10">
        <StepIndicator current={1} />

        {/* Floating sticker chips */}
        <div className="relative mt-10 flex w-full justify-center">
          <div className="absolute -left-2 top-2 hidden md:block">
            <Sticker tone="sunset" rot={-8} delay={0}>📄 PDF</Sticker>
          </div>
          <div className="absolute -right-4 -top-2 hidden md:block">
            <Sticker tone="teal" rot={9} delay={0.4}>🎬 Video</Sticker>
          </div>
          <div className="absolute right-10 top-24 hidden md:block">
            <Sticker tone="amber" rot={-4} delay={0.8}>✨ Magic</Sticker>
          </div>
        </div>

        <h1 className="pop-in mt-6 text-center text-5xl font-semibold tracking-tight md:text-6xl">
          Turn boring papers
          <br />
          <span className="shimmer-text">into bangers.</span>
        </h1>
        <p className="mt-5 max-w-lg text-center text-base text-muted-foreground md:text-lg">
          Drop a research PDF. We&rsquo;ll yank out the spiciest claims and turn one into a video. 🍿
        </p>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!isLoading) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (isLoading) return;
            const f = e.dataTransfer.files?.[0];
            if (f) handleFile(f);
          }}
          onClick={() => !isLoading && inputRef.current?.click()}
          className={`group mt-12 w-full cursor-pointer rounded-3xl border-2 border-dashed p-12 text-center transition-all ${
            dragOver
              ? "scale-[1.01] border-[var(--sunset)] bg-card"
              : "border-border bg-card/60 hover:border-[var(--amber)] hover:bg-card"
          } ${isLoading ? "opacity-70 cursor-wait" : ""}`}
          style={{ boxShadow: dragOver ? "var(--shadow-glow)" : "var(--shadow-card)" }}
        >
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
            }}
          />
          <div
            className={`mx-auto flex h-16 w-16 items-center justify-center rounded-2xl transition-transform group-hover:scale-110 group-hover:rotate-6 ${isLoading ? "" : "float"}`}
            style={{ background: "var(--gradient-sunset)" }}
          >
            <svg className="h-8 w-8 text-background" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16" />
            </svg>
          </div>
          <p className="mt-5 text-lg font-medium">
            {isLoading ? "Reading paper…" : fileName ?? (dragOver ? "Yes! Drop it 🎯" : "Drop your PDF here")}
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            {isLoading ? "Extracting the spicy bits" : "or click to browse — PDF up to 50 MB"}
          </p>

          {isLoading && (
            <div className="mx-auto mt-6 h-1 w-48 overflow-hidden rounded-full bg-secondary">
              <div
                className="h-full w-1/3 animate-[slide_1.2s_ease-in-out_infinite]"
                style={{ background: "var(--gradient-sunset)" }}
              />
            </div>
          )}
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-2 text-xs">
          {[
            { e: "🔒", t: "Private" },
            { e: "⚡", t: "~30 seconds" },
            { e: "🎬", t: "1080p" },
            { e: "🧠", t: "GPT-grade extraction" },
          ].map((x) => (
            <span
              key={x.t}
              className="wiggle-hover rounded-full border border-border bg-card/70 px-3 py-1.5 backdrop-blur transition-colors hover:bg-card"
            >
              <span className="mr-1">{x.e}</span>
              <span className="text-muted-foreground">{x.t}</span>
            </span>
          ))}
        </div>
      </main>
    </div>
  );
}

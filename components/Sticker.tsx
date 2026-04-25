import React from "react";

export function Sticker({
  children,
  tone,
  rot,
  delay,
}: {
  children: React.ReactNode;
  tone: "sunset" | "amber" | "teal";
  rot: number;
  delay: number;
}) {
  const bg =
    tone === "sunset"
      ? "var(--sunset)"
      : tone === "amber"
        ? "var(--amber)"
        : "var(--teal)";
  return (
    <div
      className="float pop-in select-none rounded-full px-3 py-1.5 text-sm font-semibold text-background shadow-[0_8px_20px_-8px_rgba(0,0,0,0.6)]"
      style={{
        background: bg,
        ["--r" as string]: `${rot}deg`,
        transform: `rotate(${rot}deg)`,
        animationDelay: `${delay}s, ${delay}s`,
      }}
    >
      {children}
    </div>
  );
}

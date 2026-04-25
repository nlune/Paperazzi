export function ConfidencePill({ v }: { v: number }) {
  const pct = Math.round(v * 100);
  return (
    <span className="shrink-0 rounded-full border border-border px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
      {pct}% confidence
    </span>
  );
}

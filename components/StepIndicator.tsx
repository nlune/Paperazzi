type Step = { id: number; label: string };

const steps: Step[] = [
  { id: 1, label: "Upload" },
  { id: 2, label: "Choose claim" },
  { id: 3, label: "Watch" },
];

export function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  return (
    <div className="flex items-center justify-center gap-3">
      {steps.map((s, i) => {
        const active = current === s.id;
        const done = current > s.id;
        return (
          <div key={s.id} className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div
                className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold transition-all ${
                  active
                    ? "bg-primary text-primary-foreground shadow-[0_0_0_4px_color-mix(in_oklab,var(--sunset)_25%,transparent)]"
                    : done
                      ? "bg-[var(--teal)] text-background"
                      : "bg-secondary text-muted-foreground"
                }`}
              >
                {done ? "✓" : s.id}
              </div>
              <span className={`text-sm ${active ? "text-foreground" : "text-muted-foreground"}`}>{s.label}</span>
            </div>
            {i < steps.length - 1 && <div className="h-px w-8 bg-border" />}
          </div>
        );
      })}
    </div>
  );
}

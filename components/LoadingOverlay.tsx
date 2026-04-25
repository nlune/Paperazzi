export function LoadingOverlay({
  progress,
  stage,
  emoji,
}: {
  progress: number;
  stage: string;
  emoji: string;
}) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center px-8 text-center">
      <div className="text-5xl float" key={emoji}>
        {emoji}
      </div>
      <div className="relative mt-4 h-20 w-20">
        <div
          className="absolute inset-0 rounded-full opacity-60 blur-xl"
          style={{ background: "var(--gradient-sunset)" }}
        />
        <div
          className="absolute inset-0 animate-spin rounded-full"
          style={{
            background:
              "conic-gradient(from 0deg, var(--sunset), var(--amber), var(--cream), var(--teal), var(--sunset))",
            animationDuration: "2.4s",
            mask: "radial-gradient(circle, transparent 56%, #000 58%)",
            WebkitMask: "radial-gradient(circle, transparent 56%, #000 58%)",
          }}
        />
        <div className="absolute inset-0 flex items-center justify-center text-lg font-semibold text-white">
          {Math.floor(progress)}%
        </div>
      </div>

      <p className="mt-6 text-base font-medium text-white">{stage}…</p>
      <p className="mt-1 text-xs text-white/50">
        Hang tight, this is the fun part 🍿
      </p>

      <div className="mt-6 h-1 w-72 max-w-full overflow-hidden rounded-full bg-white/10">
        <div
          className="h-full transition-all duration-300"
          style={{
            width: `${progress}%`,
            background: "var(--gradient-sunset)",
          }}
        />
      </div>
    </div>
  );
}

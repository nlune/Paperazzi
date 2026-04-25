import { useMemo } from "react";

const COLORS = ["var(--sunset)", "var(--amber)", "var(--cream)", "var(--teal)"];

export function Confetti({ count = 60 }: { count?: number }) {
  const bits = useMemo(
    () =>
      Array.from({ length: count }).map((_, i) => ({
        left: Math.random() * 100,
        delay: Math.random() * 0.6,
        dur: 2.4 + Math.random() * 2,
        size: 6 + Math.random() * 8,
        color: COLORS[i % COLORS.length],
        rot: Math.random() * 360,
        round: Math.random() > 0.5,
      })),
    [count],
  );
  return (
    <div className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      {bits.map((b, i) => (
        <span
          key={i}
          style={{
            position: "absolute",
            top: -20,
            left: `${b.left}%`,
            width: b.size,
            height: b.size * (b.round ? 1 : 0.4),
            background: b.color,
            borderRadius: b.round ? "50%" : 2,
            transform: `rotate(${b.rot}deg)`,
            animation: `confetti-fall ${b.dur}s ${b.delay}s linear forwards`,
          }}
        />
      ))}
    </div>
  );
}

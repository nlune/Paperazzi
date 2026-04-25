export type Claim = {
  id: string;
  title: string;
  summary: string;
  section: string;
  confidence: number; // 0-1
};

export const SAMPLE_CLAIMS: Claim[] = [
  {
    id: "c1",
    title: "Sparse attention scales sub-quadratically",
    summary: "The proposed sparse attention mechanism reduces complexity from O(n²) to O(n log n) without measurable quality loss on long-context benchmarks.",
    section: "§3.2 Method",
    confidence: 0.94,
  },
  {
    id: "c2",
    title: "Outperforms baselines on 7 of 9 tasks",
    summary: "Across the GLUE-X suite the model surpasses prior state of the art on seven downstream tasks while using 38% fewer parameters.",
    section: "§4.1 Results",
    confidence: 0.89,
  },
  {
    id: "c3",
    title: "Training cost reduced by 41%",
    summary: "End-to-end pre-training wall-clock time drops from 14.2 to 8.4 GPU-days on identical hardware due to the new routing scheme.",
    section: "§4.3 Efficiency",
    confidence: 0.86,
  },
  {
    id: "c4",
    title: "Robust under distribution shift",
    summary: "Performance degradation on out-of-distribution evaluation sets is bounded at 4.1%, compared to 11.7% for the strongest baseline.",
    section: "§5 Robustness",
    confidence: 0.78,
  },
  {
    id: "c5",
    title: "Emergent compositional reasoning",
    summary: "At >2B parameters the model demonstrates novel compositional generalization not present in any ablation, suggesting a phase transition.",
    section: "§6 Discussion",
    confidence: 0.71,
  },
];

export const STAGES = [
  { label: "Speed-reading your paper", emoji: "📖" },
  { label: "Storyboarding scenes", emoji: "🎨" },
  { label: "Conjuring visuals", emoji: "✨" },
  { label: "Dropping a beat", emoji: "🎵" },
  { label: "Rendering in 1080p", emoji: "🎬" },
];

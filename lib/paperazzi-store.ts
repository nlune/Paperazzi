"use client";

import { create } from "zustand";
import { type Claim, SAMPLE_CLAIMS } from "./constants";

interface PaperazziState {
  fileName: string | null;
  claims: Claim[];
  selectedClaimId: string | null;
  setFile: (name: string) => void;
  selectClaim: (id: string | null) => void;
  getSelectedClaim: () => Claim | null;
}

export const usePaperazziStore = create<PaperazziState>((set, get) => ({
  fileName: null,
  claims: [],
  selectedClaimId: null,
  setFile: (name) =>
    set({
      fileName: name,
      claims: SAMPLE_CLAIMS,
      selectedClaimId: null,
    }),
  selectClaim: (id) => set({ selectedClaimId: id }),
  getSelectedClaim: () => {
    const { claims, selectedClaimId } = get();
    return claims.find((c) => c.id === selectedClaimId) ?? null;
  },
}));

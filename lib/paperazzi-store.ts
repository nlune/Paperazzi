"use client";

import { create } from "zustand";
import { type Concept } from "./constants";

interface PaperazziState {
  fileName: string | null;
  concepts: Concept[];
  selectedConceptId: string | null;
  isLoading: boolean;
  setFile: (name: string | null) => void;
  setConcepts: (concepts: Concept[]) => void;
  setLoading: (loading: boolean) => void;
  selectConcept: (id: string | null) => void;
  getSelectedConcept: () => Concept | null;
}

export const usePaperazziStore = create<PaperazziState>((set, get) => ({
  fileName: null,
  concepts: [],
  selectedConceptId: null,
  isLoading: false,
  setFile: (name) =>
    set({
      fileName: name,
      concepts: [],
      selectedConceptId: null,
    }),
  setConcepts: (concepts) => set({ concepts }),
  setLoading: (loading) => set({ isLoading: loading }),
  selectConcept: (id) => set({ selectedConceptId: id }),
  getSelectedConcept: () => {
    const { concepts, selectedConceptId } = get();
    return concepts.find((c) => c.id === selectedConceptId) ?? null;
  },
}));

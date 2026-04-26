"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

export default function ConceptsRedirectPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const projectId = searchParams.get("projectId");
    router.replace(projectId ? `/video?projectId=${projectId}` : "/");
  }, [router, searchParams]);

  return null;
}

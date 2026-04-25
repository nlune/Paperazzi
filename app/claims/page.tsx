"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { PaperazziLogo } from "@/components/PaperazziLogo";

export default function ClaimsPage() {
  const router = useRouter();

  return (
    <>
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Link href="/">
          <PaperazziLogo />
        </Link>
      </header>

      <main className="mx-auto max-w-5xl px-6 pb-32 pt-6 text-center">
        <p className="text-sm text-muted-foreground">
          This page has moved to{" "}
          <button
            onClick={() => router.push("/concepts")}
            className="text-foreground font-medium hover:underline"
          >
            /concepts
          </button>
        </p>
      </main>
    </>
  );
}

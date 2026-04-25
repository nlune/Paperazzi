import type { Metadata } from "next";
import "@/styles.css";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "Paperazzi — Turn papers into videos",
  description: "Upload a PDF, pick a claim, get a video. Paperazzi turns research into stories.",
  authors: [{ name: "Paperazzi" }],
  openGraph: {
    title: "Paperazzi",
    description: "Turn papers into videos.",
    type: "website",
  },
  twitter: {
    card: "summary",
  },
  icons: {
    icon: "/favicon.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen" style={{ background: "var(--gradient-warm)" }}>
          {children}
        </div>
        <Toaster position="top-center" richColors />
      </body>
    </html>
  );
}

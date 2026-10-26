import type { Metadata } from "next";
import { DM_Sans } from "next/font/google";
import Link from "next/link";
import { Suspense, type ReactNode } from "react";

import { AskBox } from "@/components/ask-box";
import { FitLabelsProvider } from "@/components/fit-badge";
import { Nav } from "@/components/nav";
import { RunControls } from "@/components/run-controls";
import "./globals.css";

// upGrad's typeface (Circular XX) is commercially licensed; DM Sans is the closest open substitute (BRAND.md).
const dmSans = DM_Sans({ variable: "--font-dm-sans", subsets: ["latin"], weight: ["400", "500", "600", "700"] });

export const metadata: Metadata = {
  title: { default: "Founder Screener for upGrad", template: "%s | Founder Screener" },
  description: "Education founders and companies, every fact verified against its source.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${dmSans.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col font-sans">
        <FitLabelsProvider>
        <header className="bg-surface border-b border-border sticky top-0 z-30 print:hidden">
          <div className="mx-auto max-w-[1280px] px-4 md:px-6 h-16 flex items-center gap-4">
            <Link href="/" className="flex items-center gap-3 shrink-0" aria-label="Founder Screener home">
              <span className="block w-1 h-8 rounded-full bg-brand" aria-hidden />
              <span className="flex flex-col leading-tight">
                <span className="text-base font-semibold text-ink">Founder Screener</span>
                <span className="text-xs font-medium text-ink-muted">for upGrad</span>
              </span>
            </Link>
            <Nav />
            <div className="ml-auto flex items-center gap-2">
              <AskBox />
              <Suspense fallback={<div className="h-10 w-40 rounded-control bg-surface-alt animate-pulse" />}>
                <RunControls />
              </Suspense>
            </div>
          </div>
        </header>
        <main className="flex-1 mx-auto w-full max-w-[1280px] px-4 md:px-6 py-6 md:py-8 print:max-w-none print:p-0">{children}</main>
        <footer className="border-t border-border bg-surface print:hidden">
          <div className="mx-auto max-w-[1280px] px-4 md:px-6 py-4 text-xs text-ink-muted flex flex-wrap gap-x-6 gap-y-1">
            <span>Every fact links to its source. Scores are computed in code from verified claims only.</span>
            <Link className="link" href="/policy">AI Policy</Link>
            <Link className="link" href="/methodology">How scoring works</Link>
          </div>
        </footer>
        </FitLabelsProvider>
      </body>
    </html>
  );
}

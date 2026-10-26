import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { type Policy, PolicyEditor } from "@/components/policy-editor";
import { ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { policyCli } from "@/lib/policy-cli";
import { getPolicyVersions } from "@/lib/queries";

export const metadata: Metadata = { title: "AI Policy" };

async function Editor() {
  const [show, versions] = await Promise.all([
    policyCli<{ version: number | null; policy: Policy; default: Policy }>(["policy", "show"]),
    getPolicyVersions().catch(() => []),
  ]);
  if (!show.ok) return <ErrorState title="The policy could not be loaded" detail={show.detail} />;
  return <PolicyEditor active={show.data.policy} activeVersion={show.data.version} defaults={show.data.default} versions={versions} />;
}

export default function PolicyPage() {
  return (
    <>
      <PageHeader title="AI Policy" subtitle="Every rule the screener uses: fit categories, filters, scoring weights, shortlist selection and lists. Change them here, preview the impact, then save and re-score. The AI extracts, verifies and explains; code applies this policy.">
        <Link href="/methodology" className="btn-ghost shrink-0 whitespace-nowrap">How scoring works</Link>
      </PageHeader>
      <Suspense fallback={<div className="grid gap-4"><Skeleton className="h-20" /><Skeleton className="h-96" /><Skeleton className="h-96" /></div>}>
        <Editor />
      </Suspense>
    </>
  );
}

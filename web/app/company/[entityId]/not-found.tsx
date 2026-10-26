import Link from "next/link";

import { EmptyState } from "@/components/ui";

export default function NotFound() {
  return (
    <EmptyState title="Company not found">
      It is not part of any scored run. <Link href="/explore" className="link">Browse the long list</Link>.
    </EmptyState>
  );
}

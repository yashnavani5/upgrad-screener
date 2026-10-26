"use client";

import clsx from "clsx";
import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useState } from "react";

export const NAV = [
  { href: "/", label: "Overview" },
  { href: "/shortlist", label: "Shortlist" },
  { href: "/left-out", label: "Left out" },
  { href: "/explore", label: "Explore" },
  { href: "/runs", label: "Runs" },
  { href: "/policy", label: "AI Policy" },
];

/** Static fallback while the current path streams in (same links, no active highlight). */
function NavLinks({ active }: { active: (href: string) => boolean }) {
  return (
    <ul className="hidden lg:flex items-center gap-1">
      {NAV.map((n) => (
        <li key={n.href}>
          <Link href={n.href} aria-current={active(n.href) ? "page" : undefined}
                className={clsx("px-3 h-9 inline-flex items-center rounded-control text-sm font-medium",
                  active(n.href) ? "bg-brand-subtle text-brand-hover" : "text-ink-muted hover:text-ink hover:bg-surface-alt")}>
            {n.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function Nav() {
  return (
    <Suspense fallback={<nav aria-label="Main" className="flex items-center"><NavLinks active={() => false} /></nav>}>
      <ActiveNav />
    </Suspense>
  );
}

function ActiveNav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <nav aria-label="Main" className="flex items-center">
      <NavLinks active={active} />
      <div className="lg:hidden relative">
        <button type="button" className="btn-ghost h-9 px-2" aria-expanded={open} aria-controls="mobile-nav"
                aria-label="Open menu" onClick={() => setOpen((o) => !o)}>
          <Menu className="w-5 h-5" aria-hidden />
        </button>
        {open && (
          <ul id="mobile-nav" className="absolute left-0 top-11 w-56 card p-2 z-40">
            {NAV.map((n) => (
              <li key={n.href}>
                <Link href={n.href} onClick={() => setOpen(false)} aria-current={active(n.href) ? "page" : undefined}
                      className={clsx("block px-3 py-2 rounded-control text-sm font-medium",
                        active(n.href) ? "bg-brand-subtle text-brand-hover" : "text-ink hover:bg-surface-alt")}>
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </nav>
  );
}

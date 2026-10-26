"use client";

import {
  type ColumnDef, type SortingState, flexRender, getCoreRowModel, getFilteredRowModel, getPaginationRowModel,
  getSortedRowModel, useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { fitLabel, useFitLabels } from "@/components/fit-badge";
import { REASON_LABEL, score } from "@/lib/format";
import type { LongList } from "@/lib/schemas";
import { SCORE_INFO } from "@/lib/score-labels";

import { FitBadge, RegionBadge } from "./ui";

const num = (key: keyof LongList, header: string, digits = 2, hint?: string): ColumnDef<LongList> => ({
  accessorKey: key, header: hint ? () => <span title={hint}>{header}</span> : header, cell: (c) => <span className="tabular">{score(c.getValue() as number | null, digits)}</span>,
  sortingFn: "basic", sortUndefined: "last",
});

export function ExploreTable({ rows }: { rows: LongList[] }) {
  const { labels, ids: fitIds } = useFitLabels(rows.map((r) => r.fit_type));
  const router = useRouter();
  const [sorting, setSorting] = useState<SortingState>([{ id: "rank", desc: false }]);
  const [search, setSearch] = useState("");
  const [fit, setFit] = useState("all");
  const [gate, setGate] = useState("all");
  const [minFinal, setMinFinal] = useState(0);
  const data = useMemo(() => rows.filter((r) =>
    (fit === "all" || r.fit_type === fit) && (gate === "all" || r.gate_status === gate) && r.final >= minFinal), [rows, fit, gate, minFinal]);
  const columns = useMemo<ColumnDef<LongList>[]>(() => [
    { accessorKey: "rank", header: "#", cell: (c) => <span className="tabular">{c.getValue() as number}</span> },
    { accessorKey: "company", header: "Company", cell: (c) => <span className="font-medium">{c.getValue() as string}</span> },
    { id: "founders", header: "Founders", accessorFn: (r) => r.founders.map((f) => f.name).join(", "), enableSorting: false },
    { accessorKey: "fit_type", header: "Fit", cell: (c) => <FitBadge fit={c.getValue() as string | null} /> },
    { accessorKey: "region", header: "Region", cell: (c) => <RegionBadge region={c.getValue() as string} /> },
    num("final", SCORE_INFO.final.name, 3, SCORE_INFO.final.hint), num("t", SCORE_INFO.t.name, 2, SCORE_INFO.t.hint),
    num("m", SCORE_INFO.m.name, 2, SCORE_INFO.m.hint), num("n", SCORE_INFO.n.name, 2, SCORE_INFO.n.hint),
    num("i", SCORE_INFO.i.name, 2, SCORE_INFO.i.hint), num("f", SCORE_INFO.f.name, 2, SCORE_INFO.f.hint),
    num("f_acquire", "Fit: Acquire", 2, "Fit score under the Acquire category"),
    num("f_partner", "Fit: Partner", 2, "Fit score under the Partner category"),
    num("f_co_build", "Fit: Co-build", 2, "Fit score under the Co-build category"),
    num("confidence", SCORE_INFO.c.name, 2, SCORE_INFO.c.hint),
    { accessorKey: "gate_status", header: "Gate", cell: (c) => <span className={c.getValue() === "passed" ? "text-success" : "text-ink-muted"}>{c.getValue() as string}</span> },
    { accessorKey: "reason_code", header: "Reason", cell: (c) => (c.getValue() ? REASON_LABEL[c.getValue() as string] ?? (c.getValue() as string) : "-") },
  ], []);
  const table = useReactTable({
    data, columns, state: { sorting, globalFilter: search }, onSortingChange: setSorting, onGlobalFilterChange: setSearch,
    globalFilterFn: (row, _id, value: string) => `${row.original.company} ${row.original.founders.map((f) => f.name).join(" ")}`.toLowerCase().includes(value.toLowerCase()),
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(), initialState: { pagination: { pageSize: 25 } },
  });
  return (
    <div className="grid gap-4">
      <div className="card p-4 flex flex-wrap items-center gap-3">
        <input className="input w-full sm:w-64" placeholder="Search company or founder" value={search}
               onChange={(e) => setSearch(e.target.value)} aria-label="Search" data-testid="explore-search" />
        <select className="input h-10" value={fit} onChange={(e) => setFit(e.target.value)} aria-label="Fit type" data-testid="explore-fit">
          <option value="all">All fit types</option>
          {fitIds.map((k) => <option key={k} value={k}>{fitLabel(k, labels)}</option>)}
        </select>
        <select className="input h-10" value={gate} onChange={(e) => setGate(e.target.value)} aria-label="Gate" data-testid="explore-gate">
          <option value="all">All gate results</option><option value="passed">Passed gates</option><option value="failed">Failed gates</option>
        </select>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ink-muted">Min Final</span>
          <input type="number" min={0} max={1} step={0.05} value={minFinal} className="input w-20 h-10"
                 onChange={(e) => setMinFinal(Number(e.target.value) || 0)} data-testid="explore-min-final" />
        </label>
        <span className="ml-auto text-sm text-ink-muted" data-testid="explore-count">{table.getFilteredRowModel().rows.length} companies</span>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[1100px]" data-testid="explore-table">
          <thead className="bg-surface-alt">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>{hg.headers.map((h) => {
                const sorted = h.column.getIsSorted();
                return (
                  <th key={h.id} className="th" aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}>
                    {h.column.getCanSort() ? (
                      <button type="button" className="inline-flex items-center gap-1 uppercase" onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {sorted === "asc" ? <ArrowUp className="w-3 h-3" /> : sorted === "desc" ? <ArrowDown className="w-3 h-3" /> : <ArrowUpDown className="w-3 h-3 opacity-40" />}
                      </button>
                    ) : flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                );
              })}</tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((r) => (
              <tr key={r.id} className="border-t border-border hover:bg-surface-alt cursor-pointer" tabIndex={0} data-testid="explore-row"
                  onClick={() => router.push(`/company/${r.original.entity_id}`)}
                  onKeyDown={(e) => { if (e.key === "Enter") router.push(`/company/${r.original.entity_id}`); }}>
                {r.getVisibleCells().map((c) => <td key={c.id} className="td">{flexRender(c.column.columnDef.cell, c.getContext())}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" className="btn-secondary h-9" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} data-testid="page-prev">Previous</button>
        <span className="tabular">Page {table.getState().pagination.pageIndex + 1} of {Math.max(1, table.getPageCount())}</span>
        <button type="button" className="btn-secondary h-9" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} data-testid="page-next">Next</button>
        <label className="ml-auto flex items-center gap-2">
          <span className="text-ink-muted">Rows</span>
          <select className="input h-9" value={table.getState().pagination.pageSize} onChange={(e) => table.setPageSize(Number(e.target.value))} data-testid="page-size">
            {[25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
    </div>
  );
}

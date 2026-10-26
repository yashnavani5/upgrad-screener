"""Google Sheet publisher (gspread + service account).

Tabs and columns follow docs/SPEC.md section 4. Rows are keyed by a hidden entity_id column (claim_id / run_id
for the Claims / Run log tabs), never duplicated, and written with batched values updates (retry on 429/5xx).
Reviewer columns are never changed by the pipeline: their values are read back into `feedback` first and
carried with their entity when a tab is rewritten."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

SHORTLIST = ["Rank", "Founder(s)", "Company", "Website", "Fit type", "Final", "T", "I", "F", "Confidence",
             "Why they made the cut", "How they fit upGrad", "Founders: why they made the cut", "Founders: how they fit upGrad", "Evidence 1", "Evidence 2", "Evidence 3", "LinkedIn URL",
             "Last verified", "Reviewer label", "Reviewer note", "entity_id", "prev_rank"]
LEFT_OUT = ["Company", "Founder(s)", "Reason code", "Explanation", "Would have ranked", "Source", "entity_id"]
LONG_LIST = ["Rank", "Company", "Founder(s)", "Website", "Fit type", "Final", "T", "I", "F", "M", "N", "Confidence",
             "Gate", "Reason code", "entity_id", "prev_rank"]
CLAIMS = ["Company", "Subject", "Field", "Value", "Status", "Verifier confidence", "Corroborated", "Source URL",
          "Evidence span", "Retrieved at", "claim_id"]
RUN_LOG = ["run_id", "Started", "Finished", "Status", "Candidates", "Companies", "Verified claims", "Shortlisted",
           "Provider quota used", "Cost (USD)"]

TABS: dict[str, tuple[list[str], str, set[str]]] = {
    # tab: (headers, key column, hidden columns)
    "Shortlist": (SHORTLIST, "entity_id", {"entity_id", "prev_rank"}),
    "Left out": (LEFT_OUT, "entity_id", {"entity_id"}),
    "Long list": (LONG_LIST, "entity_id", {"entity_id", "prev_rank"}),
    "Claims": (CLAIMS, "claim_id", {"claim_id"}),
    "Run log": (RUN_LOG, "run_id", set()),
}
REVIEWER_COLUMNS = {"Shortlist": ["Reviewer label", "Reviewer note"]}
REVIEWER_CHOICES = ["👍", "👎", "Maybe"]
FIT_COLORS = {  # BRAND.md fit tokens (subtle background, strong text)
    "Acquire": ((0.902, 0.929, 0.984), (0.114, 0.306, 0.847)),
    "Partner": ((0.890, 0.957, 0.949), (0.059, 0.463, 0.431)),
    "Co-build": ((0.953, 0.910, 0.992), (0.494, 0.133, 0.808)),
}
RANK_CHANGED = (0.992, 0.953, 0.882)  # BRAND --warning-subtle
HEADER_BG, HEADER_FG = (0.933, 0.173, 0.235), (1.0, 1.0, 1.0)  # BRAND --branding-primary #EE2C3C, white text
TAB_ORDER = ["Shortlist", "Left out", "Long list", "Claims", "Run log"]

# Column widths in pixels; long text wraps, URLs are clipped to one line (the full link stays clickable).
WIDTHS = {
    "Rank": 56, "Company": 170, "Founder(s)": 210, "Website": 200, "Fit type": 96, "Final": 70, "T": 56, "I": 56,
    "F": 56, "M": 56, "N": 56, "Confidence": 92, "Why they made the cut": 380, "How they fit upGrad": 380,
    "Founders: why they made the cut": 380, "Founders: how they fit upGrad": 380,
    "Evidence 1": 220, "Evidence 2": 220, "Evidence 3": 220, "LinkedIn URL": 220, "Last verified": 110,
    "Reviewer label": 110, "Reviewer note": 240, "Reason code": 150, "Explanation": 460, "Would have ranked": 96,
    "Source": 240, "Gate": 80, "Subject": 170, "Field": 150, "Value": 300, "Status": 100, "Verifier confidence": 96,
    "Corroborated": 100, "Source URL": 240, "Evidence span": 420, "Retrieved at": 150, "run_id": 290, "Started": 150,
    "Finished": 150, "Candidates": 96, "Companies": 96, "Verified claims": 110, "Shortlisted": 96,
    "Provider quota used": 260, "Cost (USD)": 90,
}
WRAP = {"Founder(s)", "Why they made the cut", "How they fit upGrad", "Founders: why they made the cut", "Founders: how they fit upGrad", "Reviewer note", "Explanation", "Value",
        "Evidence span", "Provider quota used", "Company"}
CLIP = {"Website", "Evidence 1", "Evidence 2", "Evidence 3", "LinkedIn URL", "Source", "Source URL"}
NUMBER_FORMATS = {"Final": "0.000", "T": "0.00", "I": "0.00", "F": "0.00", "M": "0.00", "N": "0.00",
                  "Confidence": "0.00", "Verifier confidence": "0.00", "Cost (USD)": "$0.00"}
EVIDENCE_NOTE = "Source URLs of verified claims about this company."
NOTES = {  # header notes: hover a header in the Sheet to read what the column means
    "Rank": "Shortlist position after the pairwise AI judge and the diversity (MMR) selection.",
    "Fit type": "Best way the company could work with upGrad, per the active AI Policy categories.",
    "Final": "Overall score: T x I x F x C (weighted exponents) times the India-first location multiplier, "
             "blended with the pairwise AI judge. Computed in code, never by the AI.",
    "T": "Founder track record (0 to 1): prior exits, senior roles, notable education, awards.",
    "I": "How interesting the company is (0 to 1): momentum (recent rounds, hiring, news) and novelty.",
    "F": "Fit with upGrad (0 to 1): match to upGrad business units and wanted capabilities.",
    "M": "Momentum percentile (0 to 1).",
    "N": "Novelty percentile (0 to 1).",
    "Confidence": "Share of the expected facts that were verified against a source (0 to 1).",
    "Why they made the cut": "One line from verified claims only; every fact has a source in the Claims tab.",
    "How they fit upGrad": "One line on the fit, from verified claims only.",
    "Founders: why they made the cut": "Per founder (top 2): their verified track record, from verified claims only.",
    "Founders: how they fit upGrad": "Per founder (top 2): how their background fits an upGrad business unit or wanted capability.",
    "Evidence 1": EVIDENCE_NOTE, "Evidence 2": EVIDENCE_NOTE, "Evidence 3": EVIDENCE_NOTE,
    "Last verified": "When the claims were last re-checked against their sources.",
    "Reviewer label": "For the upGrad team: pick a label. Never overwritten by the pipeline.",
    "Reviewer note": "For the upGrad team: free text. Never overwritten by the pipeline.",
    "Reason code": "TOO_LARGE above the size ceiling; COMPETITOR scaled competitor; UPGRAD_OWNED already part of "
                   "upGrad; UNVERIFIABLE identity, role, existence or minimum facts not verified; INACTIVE; LOW_FIT "
                   "below the cut; NOT_EDUCATION; DUPLICATE_THESIS too similar to a higher pick; ACQUIRED no longer "
                   "independent; POLICY_FILTER removed by an AI Policy filter. The first rows are the curated picks.",
    "Would have ranked": "Position the company would have had by score.",
    "Status": "Verification result: supported, contradicted, not_found or unverified.",
    "Corroborated": "Confirmed by at least two independent sources.",
    "Evidence span": "Exact quote from the source that supports the claim.",
}


def col_letter(n: int) -> str:
    """1 -> A, 27 -> AA."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def with_retry(fn: Callable[[], Any], attempts: int = 6, sleep: Callable[[float], None] = time.sleep) -> Any:
    """Retry Sheets calls on 429 / 5xx with exponential backoff and jitter."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "code", None)
            if status not in (429, 500, 502, 503, 504) or i == attempts - 1:
                raise
            delay = min(64.0, 2 ** i) + random.uniform(0, 1)
            log.warning("sheets %s, retrying in %.1fs", status, delay)
            sleep(delay)
    raise AssertionError("unreachable")


@dataclass
class TabState:
    ws: Any
    rows: list[list[str]]      # existing values (incl. header)
    created: bool


class SheetPublisher:
    def __init__(self, spreadsheet: Any, sleep: Callable[[float], None] = time.sleep) -> None:
        self.sh = spreadsheet
        self.sleep = sleep
        self._state: dict[str, TabState] = {}

    @classmethod
    def from_settings(cls, settings: Any) -> SheetPublisher:
        import gspread

        info = settings.service_account_info()
        if not info or not settings.google_sheet_id:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID must be set to publish")
        gc = gspread.service_account_from_dict(info)
        return cls(with_retry(lambda: gc.open_by_key(settings.google_sheet_id)))

    @property
    def url(self) -> str | None:
        return getattr(self.sh, "url", None)

    # --- tabs ----------------------------------------------------------------------------------------

    def tab(self, name: str) -> TabState:
        if name in self._state:
            return self._state[name]
        headers, _, hidden = TABS[name]
        created = False
        try:
            ws = with_retry(lambda: self.sh.worksheet(name), sleep=self.sleep)
        except Exception as exc:
            if type(exc).__name__ != "WorksheetNotFound":
                raise
            ws = with_retry(lambda: self.sh.add_worksheet(title=name, rows=1000, cols=len(headers)), sleep=self.sleep)
            created = True
        rows = with_retry(lambda: ws.get_all_values(), sleep=self.sleep) or []
        if rows and any(rows[0]) and rows[0][: len(headers)] != headers and not created:
            # Columns changed (added/moved): rebuild the tab at the same position so hidden columns, dropdowns and
            # conditional formats match the new layout; carry existing values over by column name, so a reviewer's
            # label/note stays with its row instead of shifting into another column.
            old = {h: i for i, h in enumerate(rows[0]) if h}
            carried = [[r[old[h]] if h in old and old[h] < len(r) else "" for h in headers] for r in rows[1:]]
            index = getattr(ws, "index", None)
            size = max(1000, len(rows) + 500)
            with_retry(lambda: self.sh.del_worksheet(ws), sleep=self.sleep)
            ws = with_retry(lambda: self.sh.add_worksheet(title=name, rows=size, cols=len(headers), index=index),
                            sleep=self.sleep)
            created = True
            rows = [headers] + carried
            self._values(name, [(f"A1:{col_letter(len(headers))}{len(rows)}", rows)])
        elif not rows or rows[0][: len(headers)] != headers:
            self._values(name, [(f"A1:{col_letter(len(headers))}1", [headers])])
            rows = [headers] + rows[1:] if rows else [headers]
        if created:
            self._format(name, ws, headers, hidden)
        self._state[name] = TabState(ws, rows, created)
        return self._state[name]

    def _ensure_rows(self, st: TabState, needed: int) -> None:
        """Grow the tab's grid before writing past its last row (new tabs start at 1000 rows; Claims has more)."""
        have = getattr(st.ws, "row_count", None)
        if have is not None and needed > have:
            extra = needed - have + 500  # headroom so the next runs rarely need another resize
            with_retry(lambda: st.ws.add_rows(extra), sleep=self.sleep)

    def _values(self, tab: str, ranges: list[tuple[str, list[list[Any]]]]) -> None:
        if not ranges:
            return
        body = {"valueInputOption": "RAW",
                "data": [{"range": f"'{tab}'!{rng}", "values": vals} for rng, vals in ranges]}
        with_retry(lambda: self.sh.values_batch_update(body), sleep=self.sleep)

    def _format(self, name: str, ws: Any, headers: list[str], hidden: set[str]) -> None:
        sid = ws.id
        reqs: list[dict[str, Any]] = [
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                                       "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold"}},
        ]
        for h in hidden:
            i = headers.index(h)
            reqs.append({"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
        if "Fit type" in headers:
            i = headers.index("Fit type")
            for label, (bg, fg) in FIT_COLORS.items():
                reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": i, "endColumnIndex": i + 1}],
                    "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": label}]},
                                    "format": {"backgroundColor": _rgb(bg), "textFormat": {"foregroundColor": _rgb(fg)}}}}}})
        if "prev_rank" in headers:
            rank, prev = col_letter(headers.index("Rank") + 1), col_letter(headers.index("prev_rank") + 1)
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(headers)}],
                "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                              "values": [{"userEnteredValue": f'=AND(${prev}2<>"", ${rank}2<>${prev}2)'}]},
                                "format": {"backgroundColor": _rgb(RANK_CHANGED)}}}}})
        if name in REVIEWER_COLUMNS:
            i = headers.index("Reviewer label")
            reqs.append({"setDataValidation": {
                "range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": i, "endColumnIndex": i + 1},
                "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in REVIEWER_CHOICES]},
                         "showCustomUi": True, "strict": False}}})
        with_retry(lambda: self.sh.batch_update({"requests": reqs}), sleep=self.sleep)

    def style(self, name: str) -> None:
        """Readable layout, re-applied on every publish (idempotent): branded header with notes, column widths,
        wrapping, number formats, top-aligned rows and a filter on the header row."""
        st = self.tab(name)
        headers, _, hidden = TABS[name]
        sid = st.ws.id

        def column(i: int, **rows: int) -> dict[str, int]:
            return {"sheetId": sid, "startColumnIndex": i, "endColumnIndex": i + 1, **rows}

        reqs: list[dict[str, Any]] = [
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": _rgb(HEADER_BG), "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE",
                                "textFormat": {"bold": True, "foregroundColor": _rgb(HEADER_FG)}}},
                            "fields": "userEnteredFormat(backgroundColor,wrapStrategy,verticalAlignment,textFormat)"}},
            {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                                           "properties": {"pixelSize": 40}, "fields": "pixelSize"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1},
                            "cell": {"userEnteredFormat": {"verticalAlignment": "TOP"}},
                            "fields": "userEnteredFormat.verticalAlignment"}},
            {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0,
                                                     "endColumnIndex": len(headers)}}}},
        ]
        for i, h in enumerate(headers):
            if h in hidden:
                continue
            if h in WIDTHS:
                reqs.append({"updateDimensionProperties": {
                    "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                    "properties": {"pixelSize": WIDTHS[h]}, "fields": "pixelSize"}})
            fmt: dict[str, Any] = {}
            if h in WRAP or h in CLIP:
                fmt["wrapStrategy"] = "WRAP" if h in WRAP else "CLIP"
            if h in NUMBER_FORMATS:
                fmt["numberFormat"] = {"type": "NUMBER", "pattern": NUMBER_FORMATS[h]}
            if fmt:
                reqs.append({"repeatCell": {"range": column(i, startRowIndex=1), "cell": {"userEnteredFormat": fmt},
                                            "fields": "userEnteredFormat(" + ",".join(fmt) + ")"}})
            if h in NOTES:
                reqs.append({"repeatCell": {"range": column(i, startRowIndex=0, endRowIndex=1),
                                            "cell": {"note": NOTES[h]}, "fields": "note"}})
        with_retry(lambda: self.sh.batch_update({"requests": reqs}), sleep=self.sleep)

    def tidy(self) -> None:
        """Tabs in reading order (Shortlist first); remove Google's empty default tab once all of ours exist."""
        worksheets = getattr(self.sh, "worksheets", None)
        if worksheets is None:
            return
        tabs = with_retry(worksheets, sleep=self.sleep)
        ours = {w.title: w for w in tabs if w.title in TABS}
        order = [n for n in TAB_ORDER if n in ours]
        reqs: list[dict[str, Any]] = [
            {"updateSheetProperties": {"properties": {"sheetId": ours[n].id, "index": i}, "fields": "index"}}
            for i, n in enumerate(order)]
        if len(ours) == len(TABS):
            for w in tabs:
                if w.title in ("Sheet1", "Sheet 1"):
                    values = with_retry(w.get_all_values, sleep=self.sleep) or []
                    if not any(cell for row in values for cell in row):
                        reqs.append({"deleteSheet": {"sheetId": w.id}})
        if reqs:
            with_retry(lambda: self.sh.batch_update({"requests": reqs}), sleep=self.sleep)

    # --- reviewer feedback ---------------------------------------------------------------------------

    def reviewer_values(self, name: str = "Shortlist") -> dict[str, dict[str, str]]:
        """{entity_id: {'Reviewer label': ..., 'Reviewer note': ...}} for rows a reviewer filled in."""
        st = self.tab(name)
        headers = st.rows[0]
        key = headers.index(TABS[name][1])
        cols = {c: headers.index(c) for c in REVIEWER_COLUMNS.get(name, [])}
        out = {}
        for row in st.rows[1:]:
            if len(row) > key and row[key]:
                vals = {c: row[i] for c, i in cols.items() if i < len(row) and row[i]}
                if vals:
                    out[row[key]] = vals
        return out

    # --- writes --------------------------------------------------------------------------------------

    def upsert(self, name: str, records: list[dict[str, Any]]) -> dict[str, int]:
        """Update rows in place by key, append new ones. Reviewer columns are never written."""
        st = self.tab(name)
        headers, key_col, _ = TABS[name]
        key_i = headers.index(key_col)
        index = {row[key_i]: r for r, row in enumerate(st.rows) if r > 0 and len(row) > key_i and row[key_i]}
        skip = {headers.index(c) for c in REVIEWER_COLUMNS.get(name, [])}
        ranges: list[tuple[str, list[list[Any]]]] = []
        appended = updated = 0
        next_row = len(st.rows)
        for rec in records:
            values = [_cell(rec.get(h)) for h in headers]
            r = index.get(str(rec[key_col]))
            if r is None:
                r = next_row
                next_row += 1
                st.rows.append(values)
                index[str(rec[key_col])] = r
                appended += 1
            else:
                updated += 1
                existing = st.rows[r]
                for i in skip:  # keep whatever the reviewer typed
                    values[i] = existing[i] if i < len(existing) else ""
                st.rows[r] = values
            ranges += self._row_ranges(r + 1, values, skip)
        self._ensure_rows(st, next_row)
        self._values(name, ranges)
        return {"updated": updated, "appended": appended}

    def rewrite(self, name: str, records: list[dict[str, Any]]) -> int:
        """Replace all data rows (end of run). Reviewer values travel with their entity (read first, written back
        unchanged), rows of entities no longer present are cleared."""
        st = self.tab(name)
        headers, key_col, _ = TABS[name]
        keep = self.reviewer_values(name) if name in REVIEWER_COLUMNS else {}
        rows = []
        for rec in records:
            vals = {**rec, **keep.get(str(rec[key_col]), {})}
            rows.append([_cell(vals.get(h)) for h in headers])
        old_n = len(st.rows) - 1
        blank = [[""] * len(headers) for _ in range(max(0, old_n - len(rows)))]
        data = rows + blank
        if data:
            self._ensure_rows(st, len(data) + 1)
            self._values(name, [(f"A2:{col_letter(len(headers))}{len(data) + 1}", data)])
        st.rows = [headers] + rows
        return len(rows)

    @staticmethod
    def _row_ranges(row_no: int, values: list[str], skip: set[int]) -> list[tuple[str, list[list[Any]]]]:
        """Contiguous column runs of one row, excluding reviewer columns."""
        out, start = [], None
        for i in range(len(values) + 1):
            inside = i < len(values) and i not in skip
            if inside and start is None:
                start = i
            elif not inside and start is not None:
                out.append((f"{col_letter(start + 1)}{row_no}:{col_letter(i)}{row_no}", [values[start:i]]))
                start = None
        return out


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, float):
        return round(v, 4)
    return v if isinstance(v, (int, str)) else str(v)


def _rgb(c: tuple[float, float, float]) -> dict[str, float]:
    return {"red": c[0], "green": c[1], "blue": c[2]}

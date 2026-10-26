"""Explain writer validation/regeneration/fallback, evidence picking, and the Sheet publisher (fake gspread)."""

from __future__ import annotations

import asyncio
import re
import uuid
from types import SimpleNamespace

import pytest

from screener.config import load_yaml
from screener.explain.writer import Explanation, Segment, build_refs, pick_evidence, template, validate, write
from screener.publish.sheets import LONG_LIST, SHORTLIST, SheetPublisher, col_letter, with_retry

GAP = load_yaml("upgrad_gap_map")
VCONF = load_yaml("enrich")["verification"]
CO = uuid.uuid4()


def claim(field, value, url="https://inc42.com/a", entity=CO, conf=0.9, mode="article", source_type="press"):
    return SimpleNamespace(id=uuid.uuid4(), entity_id=entity, entity_type="company", field=field, value_json=value,
                           source_url=url, verifier_confidence=conf, source_type=source_type, meta={"text_mode": mode})


CLAIMS = [
    claim("funding_round", {"stage": "Seed", "amount_original": 5, "unit_original": "million", "currency_original": "USD",
                            "investors": ["Example Ventures"], "date": "2026-03"}),
    claim("capability", {"tag": "ai_tutoring", "label": "AI tutoring & personalization"}, "https://acme.in/", source_type="company_site"),
    claim("description", {"text": "AI tutor for Class 10 students"}, "https://acme.in/", source_type="company_site"),
]
SUBJECTS = {CO: "Acme Learning"}


def refs():
    return build_refs(CLAIMS, SUBJECTS)


GOOD = Explanation(
    why=[Segment(text="Raised a USD 5 million Seed round from Example Ventures", claim_ids=["C1"])],
    fit=[Segment(text="Acquire fit: AI tutoring & personalization for Class 10 students", claim_ids=["C2", "C3"])],
)


def test_valid_explanation_passes() -> None:
    assert validate(GOOD, refs(), "acquire", ["AI tutoring & personalization"]) == []


@pytest.mark.parametrize(
    "bad,expected",
    [
        (Explanation(why=[Segment(text="Raised money", claim_ids=[])], fit=GOOD.fit), "no valid claim id"),
        (Explanation(why=[Segment(text="Raised money", claim_ids=["C9"])], fit=GOOD.fit), "no valid claim id"),
        (Explanation(why=[Segment(text="Raised USD 50 million", claim_ids=["C1"])], fit=GOOD.fit), "numbers not in its claims"),
        (Explanation(why=[Segment(text=" ".join(["word"] * 31), claim_ids=["C1"])], fit=GOOD.fit), "31 words"),
        (Explanation(why=GOOD.why, fit=[Segment(text="Great fit for AI tutoring & personalization", claim_ids=["C2"])]), "fit type"),
        (Explanation(why=GOOD.why, fit=[Segment(text="Acquire fit for students", claim_ids=["C3"])]), "units/capabilities"),
    ],
)
def test_invalid_explanations_are_rejected(bad, expected) -> None:
    errors = validate(bad, refs(), "acquire", ["AI tutoring & personalization"])
    assert any(expected in e for e in errors), errors


class ScriptedGemini:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    async def generate_json(self, prompt, schema, model="fast", *, system=None, use_cache=True):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_writer_regenerates_with_errors_then_accepts() -> None:
    bad = Explanation(why=[Segment(text="Raised USD 50 million", claim_ids=["C1"])], fit=GOOD.fit)
    g = ScriptedGemini([bad, GOOD])
    w = asyncio.run(write(g, "Acme Learning", "acquire", CLAIMS, SUBJECTS, GAP))
    assert w.method == "llm" and w.attempts == 2
    assert "REJECTED" in g.prompts[1] and "numbers not in its claims" in g.prompts[1]
    assert set(w.why_ids) == {str(CLAIMS[0].id)} and set(w.fit_ids) == {str(CLAIMS[1].id), str(CLAIMS[2].id)}
    assert len(w.why_line.split()) <= 30 and "Acquire" in w.fit_line


def test_writer_falls_back_to_claim_template() -> None:
    bad = Explanation(why=[Segment(text="Huge unicorn", claim_ids=[])], fit=[Segment(text="x", claim_ids=[])])
    w = asyncio.run(write(ScriptedGemini([bad, bad, bad]), "Acme Learning", "acquire", CLAIMS, SUBJECTS, GAP))
    assert w.method == "template" and w.errors
    assert "Seed" in w.why_line and w.fit_line.startswith("Acquire fit: AI tutoring")
    assert len(w.why_line.split()) <= 30


def test_template_without_claims_is_safe() -> None:
    w = template({}, "partner", ["Study abroad"])
    assert w.fit_line == "Partner fit: Study abroad." and w.why_ids == []


def test_evidence_prefers_primary_then_diverse_domains_and_snippets_last() -> None:
    claims = [
        claim("funding_round", {}, "https://inc42.com/a", conf=0.99),
        claim("funding_round", {}, "https://inc42.com/b", conf=0.98),
        claim("description", {}, "https://acme.in/about", conf=0.5),
        claim("current_role", {}, "https://www.crunchbase.com/x", conf=0.99, mode="snippet"),
        claim("revenue", {}, "https://entrackr.com/r", conf=0.9),
    ]
    ev = pick_evidence(claims, set(), "acme.in", VCONF)
    assert [e["domain"] for e in ev] == ["acme.in", "inc42.com", "entrackr.com"]
    assert ev[0]["primary"] is True


# --- Sheet publisher ---------------------------------------------------------------------------------


class WorksheetNotFound(Exception):
    pass


class FakeWorksheet:
    def __init__(self, title, sid, rows=1000):
        self.title, self.id, self.grid, self.row_count = title, sid, {}, rows

    def add_rows(self, n):
        self.row_count += n

    def get_all_values(self):
        if not self.grid:
            return []
        n = max(r for r, _ in self.grid)
        m = max(c for _, c in self.grid)
        return [[str(self.grid.get((r, c), "")) for c in range(1, m + 1)] for r in range(1, n + 1)]


class FakeSpreadsheet:
    url = "https://docs.google.com/spreadsheets/d/fake"

    def __init__(self, fail_first=0):
        self.tabs, self.requests, self.value_calls, self.fail_first = {}, [], 0, fail_first

    def worksheet(self, name):
        if name not in self.tabs:
            raise WorksheetNotFound(name)
        return self.tabs[name]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title, len(self.tabs) + 1, rows)
        return self.tabs[title]

    def values_batch_update(self, body):
        if self.fail_first:
            self.fail_first -= 1
            raise SimpleNamespace_error(429)
        self.value_calls += 1
        for d in body["data"]:
            tab, rng = d["range"].split("!")
            ws = self.tabs[tab.strip("'")]
            m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
            c0, r0 = _col(m.group(1)), int(m.group(2))
            if r0 + len(d["values"]) - 1 > ws.row_count:  # like Google: "Range ... exceeds grid limits"
                raise SimpleNamespace_error(400)
            for i, row in enumerate(d["values"]):
                for j, v in enumerate(row):
                    ws.grid[(r0 + i, c0 + j)] = v

    def batch_update(self, body):
        self.requests += body["requests"]


class SimpleNamespace_error(Exception):  # noqa: N801 - mimics gspread APIError(response.status_code)
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.response = SimpleNamespace(status_code=code)


def _col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def rec(eid, rank, company="Acme"):
    return {"Rank": rank, "Company": company, "Fit type": "Acquire", "Final": 0.5, "entity_id": eid}


def test_col_letter() -> None:
    assert [col_letter(n) for n in (1, 26, 27, 52)] == ["A", "Z", "AA", "AZ"]


def test_tab_created_with_headers_and_formatting() -> None:
    sh = FakeSpreadsheet()
    pub = SheetPublisher(sh, sleep=lambda s: None)
    pub.tab("Shortlist")
    ws = sh.tabs["Shortlist"]
    assert ws.get_all_values()[0] == SHORTLIST
    kinds = [next(iter(r)) for r in sh.requests]
    assert "updateSheetProperties" in kinds and "setDataValidation" in kinds and "updateDimensionProperties" in kinds
    assert sum(1 for k in kinds if k == "addConditionalFormatRule") == 4  # 3 fit colors + rank changed
    dv = next(r for r in sh.requests if "setDataValidation" in r)["setDataValidation"]["rule"]["condition"]["values"]
    assert [v["userEnteredValue"] for v in dv] == ["👍", "👎", "Maybe"]


def test_style_is_readable_and_idempotent() -> None:
    sh = FakeSpreadsheet()
    pub = SheetPublisher(sh, sleep=lambda s: None)
    pub.tab("Shortlist")
    created = len(sh.requests)
    pub.style("Shortlist")
    first = sh.requests[created:]
    pub.style("Shortlist")
    assert sh.requests[created + len(first):] == first  # same requests every publish: nothing piles up
    kinds = [next(iter(r)) for r in first]
    assert "setBasicFilter" in kinds and "addConditionalFormatRule" not in kinds
    cells = [r["repeatCell"] for r in first if "repeatCell" in r]
    assert any(c["cell"].get("note", "").startswith("Overall score") for c in cells)  # header note on Final
    final_col = SHORTLIST.index("Final")
    assert any(c["range"].get("startColumnIndex") == final_col
               and c["cell"].get("userEnteredFormat", {}).get("numberFormat", {}).get("pattern") == "0.000" for c in cells)
    widths = [r["updateDimensionProperties"] for r in first if "updateDimensionProperties" in r]
    hidden = SHORTLIST.index("entity_id")
    assert not any(w["range"].get("startIndex") == hidden and w["range"]["dimension"] == "COLUMNS" for w in widths)


def test_tabs_grow_past_the_initial_grid() -> None:
    sh = FakeSpreadsheet()
    pub = SheetPublisher(sh, sleep=lambda s: None)
    assert pub.rewrite("Long list", [rec(f"e{i}", i) for i in range(1, 1501)]) == 1500
    assert sh.tabs["Long list"].row_count >= 1501
    assert pub.upsert("Long list", [rec(f"n{i}", i) for i in range(1, 801)])["appended"] == 800
    assert sh.tabs["Long list"].row_count >= 2301 and len(sh.tabs["Long list"].get_all_values()) == 2301


def test_upsert_by_entity_never_duplicates() -> None:
    sh = FakeSpreadsheet()
    pub = SheetPublisher(sh, sleep=lambda s: None)
    assert pub.upsert("Long list", [rec("e1", 1), rec("e2", 2)]) == {"updated": 0, "appended": 2}
    assert pub.upsert("Long list", [rec("e2", 1, "Beta"), rec("e3", 3)]) == {"updated": 1, "appended": 1}
    rows = sh.tabs["Long list"].get_all_values()
    assert len(rows) == 4  # header + 3 entities
    ids = [r[LONG_LIST.index("entity_id")] for r in rows[1:]]
    assert ids == ["e1", "e2", "e3"]
    assert rows[2][LONG_LIST.index("Company")] == "Beta"


def test_reviewer_columns_are_never_overwritten() -> None:
    sh = FakeSpreadsheet()
    pub = SheetPublisher(sh, sleep=lambda s: None)
    pub.rewrite("Shortlist", [rec("e1", 1), rec("e2", 2)])
    ws = sh.tabs["Shortlist"]
    label, note = SHORTLIST.index("Reviewer label") + 1, SHORTLIST.index("Reviewer note") + 1
    ws.grid[(3, label)], ws.grid[(3, note)] = "👍", "call next week"  # reviewer edits e2's row
    pub2 = SheetPublisher(sh, sleep=lambda s: None)  # next run re-reads the sheet
    assert pub2.reviewer_values() == {"e2": {"Reviewer label": "👍", "Reviewer note": "call next week"}}
    pub2.rewrite("Shortlist", [rec("e2", 1), rec("e9", 2)])  # e2 moves to row 2, e1 drops out
    rows = ws.get_all_values()
    e2 = next(r for r in rows[1:] if r[SHORTLIST.index("entity_id")] == "e2")
    assert e2[label - 1] == "👍" and e2[note - 1] == "call next week"  # travelled with its entity
    assert not any(r[SHORTLIST.index("entity_id")] == "e1" for r in rows[1:])
    pub2.upsert("Shortlist", [rec("e2", 1, "Renamed")])
    e2 = next(r for r in ws.get_all_values()[1:] if r[SHORTLIST.index("entity_id")] == "e2")
    assert e2[label - 1] == "👍" and e2[SHORTLIST.index("Company")] == "Renamed"


def test_retry_on_429() -> None:
    sh = FakeSpreadsheet(fail_first=2)
    slept = []
    pub = SheetPublisher(sh, sleep=slept.append)
    pub.upsert("Run log", [{"run_id": "r1", "Status": "succeeded"}])
    assert len(slept) == 2 and sh.tabs["Run log"].get_all_values()[1][0] == "r1"


def test_retry_gives_up_on_other_errors() -> None:
    def boom():
        raise SimpleNamespace_error(403)

    with pytest.raises(SimpleNamespace_error):
        with_retry(boom, sleep=lambda s: None)


def test_generated_copy_has_no_em_or_en_dashes() -> None:
    from screener.explain.writer import join, plain

    assert plain("AI tutor — for Class 10") == "AI tutor, for Class 10"
    assert plain("Seed round – 2026") == "Seed round, 2026"
    line = join([Segment(text="Raised Seed — led by Example Ventures", claim_ids=["C1"])])
    assert "—" not in line and "–" not in line


def test_curated_left_out_one_per_category_india_first_never_upgrad() -> None:
    from screener.explain.people import Candidate, pick_curated

    rows = [Candidate("upgrad", "UPGRAD_OWNED", 3, "india"), Candidate("big-us", "TOO_LARGE", 5, "outside"),
            Candidate("big-in", "TOO_LARGE", 9, "india"), Candidate("thin", "UNVERIFIABLE", 8, "unknown"),
            Candidate("near", "LOW_FIT", 20, "india"), Candidate("dup", "DUPLICATE_THESIS", 16, "outside")]
    picks = {cat: c.key for cat, c in pick_curated(rows)}
    assert picks == {"big": "big-in", "evidence": "thin", "close": "near"}  # India first within each category

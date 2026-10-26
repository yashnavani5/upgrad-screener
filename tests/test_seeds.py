"""Auto calibration seeds: the rule, merging with reviewer entries, and the YAML writer."""

from pathlib import Path

from screener.score.seeds import ORIGIN, SeedRow, merge, pick, positive_reason, save


def row(name: str, **kw) -> SeedRow:
    base = dict(company=name, website=f"https://{name.lower()}.example", rank=10, reason_code=None, reason_text=None,
                reason_url=None, geo="india", tags=("ai_tutoring",), recent_rounds=1, top_investor=False,
                evidence_url=f"https://news.example/{name.lower()}")
    base.update(kw)
    return SeedRow(**base)


def test_positive_needs_india_gates_fit_and_external_validation():
    assert positive_reason(row("A"))
    assert positive_reason(row("A", recent_rounds=0, top_investor=True))
    assert positive_reason(row("A", reason_code="LOW_FIT"))  # soft exclusions do not disqualify a positive
    assert positive_reason(row("A", geo="outside")) is None
    assert positive_reason(row("A", geo="unknown")) is None
    assert positive_reason(row("A", tags=())) is None
    assert positive_reason(row("A", recent_rounds=0)) is None
    for code in ("UNVERIFIABLE", "INACTIVE", "TOO_LARGE", "NOT_EDUCATION", "COMPETITOR", "UPGRAD_OWNED"):
        assert positive_reason(row("A", reason_code=code)) is None


def test_pick_uses_only_claim_backed_negatives_and_ranks_them():
    rows = [
        row("Good"),
        row("NoEvidence", evidence_url=None),
        row("Big", reason_code="TOO_LARGE", reason_text="raised too much", reason_url="https://a.example", rank=40),
        row("Rival", reason_code="COMPETITOR", reason_text="listed", reason_url="https://b.example", rank=5),
        row("NoSource", reason_code="NOT_EDUCATION", reason_text="x", reason_url=None),
        row("Listed", reason_code="COMPETITOR", reason_text="on the list", reason_url=None, rank=60),
        row("Dead", reason_code="INACTIVE", reason_text="x", reason_url="https://c.example"),  # not a negative seed
    ]
    pos, neg = pick(rows, max_positives=10, max_negatives=10)
    assert [p["company"] for p in pos] == ["Good"]
    assert [n["company"] for n in neg] == ["Rival", "Big", "Listed"]  # highest-ranked negative first
    assert neg[2]["source_url"] == "https://listed.example"           # config-listed: the company's own site
    assert all(e["origin"] == ORIGIN and e["source_url"] for e in pos + neg)
    _, neg1 = pick(rows, max_positives=10, max_negatives=1)
    assert [n["company"] for n in neg1] == ["Rival"]


def test_merge_keeps_reviewer_entries_and_never_crosses_sides():
    existing = {
        "positives": [{"company": "Reviewer Pick", "origin": "reviewer"}, {"company": "Old Auto", "origin": ORIGIN}],
        "negatives": ["Plain Name Negative"],
    }
    auto_pos = [{"company": "New Auto", "origin": ORIGIN}, {"company": "plain name negative", "origin": ORIGIN}]
    auto_neg = [{"company": "Reviewer Pick", "origin": ORIGIN}, {"company": "Bad Co", "origin": ORIGIN}]
    out = merge(existing, auto_pos, auto_neg)
    names = lambda side: [e["company"] if isinstance(e, dict) else e for e in out[side]]  # noqa: E731
    assert names("positives") == ["Reviewer Pick", "New Auto"]          # old auto entry replaced
    assert names("negatives") == ["Plain Name Negative", "Bad Co"]      # reviewer entries win across sides


def test_save_keeps_header_comments(tmp_path: Path):
    path = tmp_path / "seeds.yaml"
    path.write_text("# header stays\npositives: []\nnegatives: []\n", encoding="utf-8")
    save(path, {"positives": [{"company": "A", "origin": ORIGIN}], "negatives": []})
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# header stays")
    assert "company: A" in text and "negatives: []" in text

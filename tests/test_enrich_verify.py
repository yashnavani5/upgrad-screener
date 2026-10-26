"""Enrichment + verification rules: grounding, INR conversion, corroboration, liveness, identity."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from screener.config import load_yaml
from screener.enrich.claims import ClaimDraft, grounded
from screener.enrich.money import normalize_currency, same_amount, to_absolute, to_usd, year_of
from screener.enrich.pages import window_text
from screener.enrich.schemas import BASE_RULES, REQUIRED
from screener.verify.rules import (
    ClaimView, IdentityInput, LivenessInput, corroborate, equivalent, identity_verified, is_primary, liveness,
    months_between, parse_date,
)
from screener.verify.verifier import statement

CONF = load_yaml("enrich")
VCONF = CONF["verification"]
RATE = CONF["currency"]["inr_per_usd"]
CO = uuid.uuid4()

ARTICLE = (
    "Bengaluru-based edtech startup Acme Learning has raised Rs 40 crore (about $4.3 million) in a Series A "
    "round led by Example Ventures, with participation from Seed Fund. Acme Learning's revenue for FY24 stood at "
    "Rs 12.5 crore, its filings show."
)


# --- grounding ---------------------------------------------------------------------------------------


def draft(span: str) -> ClaimDraft:
    return ClaimDraft("company", CO, "funding_round", {"stage": "Series A"}, "https://news.example/a", "press",
                      span, datetime.now(timezone.utc))


def test_grounding_accepts_verbatim_and_lightly_reformatted_spans() -> None:
    d = draft("has raised Rs 40 crore (about $4.3 million) in a Series A round")
    assert grounded(d, ARTICLE) and d.meta["grounding_score"] == 100.0
    assert grounded(draft("HAS RAISED  Rs 40 crore (about $4.3 million)   in a Series A round"), ARTICLE)


def test_grounding_rejects_fabricated_or_altered_spans() -> None:
    assert not grounded(draft("has raised Rs 400 crore (about $43 million) in a Series C round led by Tiger"), ARTICLE)
    assert not grounded(draft("Acme Learning was acquired by BigCo in 2025"), ARTICLE)
    assert not grounded(draft(""), ARTICLE)


def test_extraction_prompt_carries_required_instruction() -> None:
    assert REQUIRED == "Return null if the text does not state it. Copy evidence_span verbatim."
    assert REQUIRED in BASE_RULES


# --- INR conversion ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount,unit,absolute",
    [(40, "crore", 4e8), (12.5, "Cr", 1.25e8), (75, "lakh", 7.5e6), (5, "million", 5e6), (1.2, "bn", 1.2e9), (250000, None, 250000)],
)
def test_units(amount, unit, absolute) -> None:
    assert to_absolute(amount, unit) == pytest.approx(absolute)


def test_inr_converts_at_configured_rate_and_keeps_original() -> None:
    m = to_usd(40, "INR", "crore", RATE)
    assert m["amount_usd"] == pytest.approx(4e8 / RATE, rel=1e-6)
    assert (m["amount_original"], m["unit_original"], m["currency_original"]) == (40, "crore", "INR")
    assert m["absolute_original"] == 4e8


def test_usd_is_not_converted_and_unknowns_return_none() -> None:
    assert to_usd(5, "$", "million", RATE)["amount_usd"] == 5e6
    assert to_usd(5, "EUR", "million", RATE) is None       # unsupported currency: never guessed
    assert to_usd(5, "INR", "gazillion", RATE) is None     # unknown unit
    assert to_usd(None, "INR", "crore", RATE) is None
    assert normalize_currency("Rs.") == "INR" and normalize_currency("₹") == "INR"


def test_same_amount_and_year() -> None:
    assert same_amount(4.3e6, 4.5e6, 0.10) and not same_amount(4.3e6, 6e6, 0.10)
    assert year_of("March 2024") == 2024 and year_of(None) is None


# --- corroboration -----------------------------------------------------------------------------------


def cv(field: str, value: dict, url: str, status: str = "supported", entity=CO) -> ClaimView:
    return ClaimView(uuid.uuid4(), entity, field, value, url, status, 0.9)


SERIES_A = {"stage": "Series A", "date": "2026-03", "amount_usd": 4.32e6}


def test_funding_needs_two_independent_domains() -> None:
    a = cv("funding_round", SERIES_A, "https://inc42.com/buzz/acme")
    b = cv("funding_round", {**SERIES_A, "amount_usd": 4.3e6}, "https://entrackr.com/acme")
    one = corroborate([a], None, VCONF)
    assert one[a.id].needed and not one[a.id].corroborated
    two = corroborate([a, b], None, VCONF)
    assert two[a.id].corroborated and two[a.id].domains == ("entrackr.com", "inc42.com")


def test_same_domain_twice_is_not_independent() -> None:
    a = cv("funding_round", SERIES_A, "https://inc42.com/buzz/acme")
    b = cv("funding_round", SERIES_A, "https://inc42.com/features/acme-again")
    assert not corroborate([a, b], None, VCONF)[a.id].corroborated


def test_primary_source_alone_corroborates() -> None:
    company_site = cv("funding_round", SERIES_A, "https://www.acmelearning.in/press")
    assert corroborate([company_site], "acmelearning.in", VCONF)[company_site.id].corroborated
    gov = cv("revenue", {"fiscal_year": "FY24", "amount_usd": 1.35e6}, "https://www.mca.gov.in/filing/123")
    assert corroborate([gov], None, VCONF)[gov.id].primary
    acq = cv("acquired", {"acquirer": "BigCo Education"}, "https://www.bigcoeducation.com/news/acme")
    assert corroborate([acq], None, VCONF)[acq.id].corroborated  # acquirer's own release


def test_different_facts_do_not_corroborate_each_other() -> None:
    a = cv("funding_round", SERIES_A, "https://inc42.com/a")
    b = cv("funding_round", {"stage": "Seed", "date": "2024", "amount_usd": 1e6}, "https://entrackr.com/b")
    assert not corroborate([a, b], None, VCONF)[a.id].corroborated


def test_unsupported_claims_do_not_count_and_other_fields_need_nothing() -> None:
    a = cv("funding_round", SERIES_A, "https://inc42.com/a")
    b = cv("funding_round", SERIES_A, "https://entrackr.com/b", status="not_found")
    assert not corroborate([a, b], None, VCONF)[a.id].corroborated
    city = cv("city", {"city": "Bengaluru"}, "https://inc42.com/a")
    assert corroborate([city], None, VCONF)[city.id] .needed is False


def test_equivalence_rules() -> None:
    rev_a = cv("revenue", {"fiscal_year": "FY24", "amount_usd": 1.35e6}, "https://a.com")
    rev_b = cv("revenue", {"fiscal_year": "FY2023-24", "amount_usd": 1.40e6}, "https://b.com")
    assert equivalent(rev_a, rev_b, 0.10)  # FY24 == FY2023-24, amounts within 10%
    no_amt_a = cv("funding_round", {"stage": "Series A", "date": "2026-03"}, "https://a.com")
    no_amt_b = cv("funding_round", {"stage": "series-a", "date": "2026"}, "https://b.com")
    assert equivalent(no_amt_a, no_amt_b, 0.10)
    exit_a = cv("prior_exit", {"company": "Toppr"}, "https://a.com")
    exit_b = cv("prior_exit", {"company": "Toppr Technologies"}, "https://b.com")
    assert equivalent(exit_a, exit_b, 0.10)


def test_is_primary() -> None:
    assert is_primary("https://acme.in/about", "acme.in", [], VCONF)
    assert is_primary("https://www.mca.gov.in/x", None, [], VCONF)
    assert not is_primary("https://www.tofler.in/acme", "acme.in", [], VCONF)  # aggregator, not primary


# --- liveness ----------------------------------------------------------------------------------------

TODAY = date(2026, 10, 25)


def live(site, activity, founder) -> tuple[str, str]:
    return liveness(LivenessInput(site, activity, founder, TODAY, 12))


def test_active_when_site_resolves_and_recent_activity() -> None:
    assert live(True, date(2026, 6, 1), None)[0] == "active"
    assert live(True, date(2026, 6, 1), True)[0] == "active"


def test_inactive_only_on_combined_evidence() -> None:
    status, reason = live(False, date(2025, 9, 1), None)
    assert status == "inactive" and "website does not resolve" in reason and "since 2025-09-01" in reason
    assert live(False, None, None)[0] == "inactive"                 # dead site, nothing dated anywhere
    status, reason = live(True, date(2025, 9, 1), False)
    assert status == "inactive" and "founder no longer listed" in reason


def test_one_weak_signal_is_unknown_not_inactive() -> None:
    # The old rule excluded these (e.g. NxtWave on an old news date, Leap Scholar on one failed fetch).
    assert live(True, date(2025, 9, 1), True)[0] == "unknown"       # live site, old news only
    assert live(False, date(2026, 6, 1), None)[0] == "unknown"      # dead fetch, but recent activity elsewhere
    assert live(True, date(2026, 6, 1), False)[0] == "active"       # founder change alone, recent activity


def test_unknown_without_enough_evidence() -> None:
    assert live(None, None, None)[0] == "unknown"
    assert live(True, None, None)[0] == "unknown"   # no dated activity found
    assert live(None, date(2026, 6, 1), None)[0] == "unknown"


def test_twelve_month_boundary() -> None:
    assert months_between(date(2025, 10, 25), TODAY) == 12
    assert live(True, date(2025, 10, 25), None)[0] == "active"     # exactly 12 months
    assert live(True, date(2025, 10, 24), None)[0] == "active"     # 12 months (day earlier)
    assert live(True, date(2025, 9, 24), None)[0] == "unknown"     # 13 months, site still up
    assert live(False, date(2025, 9, 24), None)[0] == "inactive"   # 13 months, site dead


def test_parse_date() -> None:
    assert parse_date("2026-03-14") == date(2026, 3, 14)
    assert parse_date("2026-03") == date(2026, 3, 1)
    assert parse_date("2026") == date(2026, 1, 1)
    assert parse_date("March 2026") is None and parse_date(None) is None


# --- identity ----------------------------------------------------------------------------------------

F = uuid.uuid4()


def test_identity_needs_supported_non_linkedin_claim_linking_founder_and_company() -> None:
    linkedin = cv("current_role", {"company": "Acme Learning", "role": "Co-founder"}, "https://in.linkedin.com/in/asha", entity=F)
    site = cv("current_role", {"company": "Acme Learning", "role": "Co-founder"}, "https://acme.in/team", entity=F)
    other_co = cv("current_role", {"company": "Zeta", "role": "Founder"}, "https://zeta.io/team", entity=F)
    unverified = cv("current_role", {"company": "Acme Learning", "role": "CEO"}, "https://inc42.com/a", status="not_found", entity=F)
    base = dict(founder_name="Asha Rao", company_names=["Acme Learning", "Acme Learning Pvt Ltd"])
    assert not identity_verified(IdentityInput(**base, founder_claims=[linkedin]))
    assert not identity_verified(IdentityInput(**base, founder_claims=[other_co, unverified]))
    assert identity_verified(IdentityInput(**base, founder_claims=[linkedin, site]))


def test_identity_via_registry_director() -> None:
    director = cv("director", {"name": "Dr. Asha Rao"}, "https://www.tofler.in/acme")
    assert identity_verified(IdentityInput("Asha Rao", ["Acme Learning"], [], [director]))


# --- verifier input ----------------------------------------------------------------------------------


def test_statement_reads_like_a_claim_and_has_no_evidence_span() -> None:
    s = statement("funding_round", {"stage": "Series A", "amount_original": 40, "unit_original": "crore",
                                    "currency_original": "INR", "investors": ["Example Ventures"], "date": "2026-03"}, "Acme Learning")
    assert s == "Acme Learning raised Series A INR 40 crore from Example Ventures (2026-03)."
    assert statement("current_role", {"company": "Acme", "role": "Co-founder"}, "Asha Rao") == "Asha Rao is Co-founder of Acme."


def test_window_text_keeps_claim_regions_of_long_pages() -> None:
    text = "intro " * 4000 + "Acme raised Rs 40 crore in a Series A" + " filler" * 4000
    w = window_text(text, ["Acme raised Rs 40 crore"], 6000)
    assert "Acme raised Rs 40 crore" in w and len(w) <= 6000
    assert window_text("short page", ["x"], 6000) == "short page"


def test_published_date_is_part_of_page_text() -> None:
    from screener.enrich.pages import page_text, published_line
    from screener.http import FetchResult

    html = ('<html><head><title>Acme raises Rs 40 crore</title><meta property="article:published_time" '
            'content="2026-06-30T10:00:00+05:30"></head><body><article><p>' + "Acme raised money. " * 30 + "</p></article></body></html>")
    r = FetchResult(url="https://news.example/a", final_url="https://news.example/a", status=200, html=html,
                    text="Acme raised money.", fetched_at=datetime.now(timezone.utc), method="httpx", outcome="ok")
    text = page_text(r, "article")
    assert published_line("2026-06-30") in text.splitlines()[1]
    assert text.startswith("Acme raises Rs 40 crore")

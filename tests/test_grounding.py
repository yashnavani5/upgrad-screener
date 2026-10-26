from __future__ import annotations

import pytest

from screener.scouts.grounding import check, name_in_text, normalize, span_in_source

SOURCE = (
    "Bengaluru-based edtech startup Acme Learning has raised $5 million in a seed round led by "
    "Example Ventures. Founded in 2023 by Asha Rao and Vikram Singh, the company offers AI tutoring "
    "for Class 9–12 students in Hindi and Tamil."
)


def test_exact_span_is_grounded() -> None:
    assert span_in_source("edtech startup Acme Learning has raised $5 million", SOURCE)


def test_case_whitespace_and_dash_differences_are_tolerated() -> None:
    assert span_in_source("EDTECH  startup   acme learning has raised $5 million", SOURCE)
    assert span_in_source("AI tutoring for Class 9-12 students", SOURCE)  # en dash vs hyphen


def test_small_llm_typo_is_tolerated() -> None:
    assert span_in_source("Founded in 2023 by Asha Rao and Vikram Sing", SOURCE)


def test_fabricated_span_is_rejected() -> None:
    assert not span_in_source("Acme Learning has raised $50 million in a Series C led by Tiger Global", SOURCE)
    assert not span_in_source("BrightPath Academy raised funding from Sequoia", SOURCE)


def test_too_short_span_is_rejected() -> None:
    assert not span_in_source("Acme", SOURCE)
    assert not span_in_source(None, SOURCE)


def test_name_must_be_inside_span() -> None:
    span = "has raised $5 million in a seed round led by Example Ventures"
    assert span_in_source(span, SOURCE)
    assert not check(span, SOURCE, "Acme Learning").ok
    assert check("edtech startup Acme Learning has raised $5 million", SOURCE, "Acme Learning").ok


def test_check_reports_reason() -> None:
    assert check("invented sentence that is not there at all", SOURCE, "Acme").reason == "evidence_span not found in source text"


def test_short_names_need_word_boundary() -> None:
    assert name_in_text("Eka", "Eka raises funding for tutors")
    assert not name_in_text("Eka", "Rekha raises funding")  # no fuzzy match for short names


@pytest.mark.parametrize("name,ok", [("Asha Rao", True), ("Vikram Singh", True), ("Priya Nair", False)])
def test_founder_names_checked_against_source(name: str, ok: bool) -> None:
    assert name_in_text(name, SOURCE) is ok


def test_normalize() -> None:
    assert normalize("  It’s  “Acme” Learning ") == "it's \"acme\" learning"


def test_numbers_in_span_must_match_exactly() -> None:
    src = "Acme Learning has raised Rs 40 crore (about $4.3 million) in a Series A round led by Example Ventures."
    assert span_in_source("has raised Rs 40 crore (about $4.3 million) in a Series A", src)
    assert not span_in_source("has raised Rs 400 crore (about $4.3 million) in a Series A", src)   # 400 vs 40
    assert not span_in_source("has raised Rs 40 crore (about $43 million) in a Series A", src)     # 43 vs 4.3
    assert span_in_source("raised Rs 1,200 crore in revenue", "it raised Rs 1200 crore in revenue last year")

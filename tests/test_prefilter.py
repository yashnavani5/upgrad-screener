from __future__ import annotations

import pytest

from screener.config import load_yaml
from screener.scouts.prefilter import Prefilter


@pytest.fixture(scope="module")
def pf() -> Prefilter:
    return Prefilter(load_yaml("sources")["prefilter_keywords"])


@pytest.mark.parametrize(
    "text",
    [
        "Bengaluru-based edtech startup raises $5M seed round",
        "Ed-Tech platform for Class 10 students launches in Hindi",
        "Upskilling startup Acme Learning bags pre-Series A",
        "AI tutor app raises funding",
        "Test-prep company acquires rival",
        "testprep platform", "K12 school software", "k-12 platform",
        "NEET and JEE coaching startup expands",
        "Study abroad marketplace raises Series B",
        "Corporate training platform for frontline workers",
        "Online learning provider files for IPO",
    ],
)
def test_education_items_pass(pf: Prefilter, text: str) -> None:
    assert pf.matches(text)


@pytest.mark.parametrize(
    "text",
    [
        "Fintech startup raises $20M Series A",
        "Quick-commerce firm cuts losses",
        "Reduce costs with new SaaS",            # 'edu' inside 'reduce' must not match
        "Tutorial: deploy with Docker",          # 'tutor' inside 'tutorial' must not match
        "Scholarly debate on monetary policy",   # 'school' inside 'scholarly' must not match
        "Machine learning startup raises",       # generic ML is not education
        "",
    ],
)
def test_non_education_items_fail(pf: Prefilter, text: str) -> None:
    assert not pf.matches(text)


def test_matches_any_of_several_fields(pf: Prefilter) -> None:
    assert pf.matches("Startup raises $3M", "The company builds tools for teachers")
    assert pf.matches(None, "edtech")


def test_matched_reports_keywords(pf: Prefilter) -> None:
    assert pf.matched("EdTech for students and teachers") == ["edtech", "students", "teachers"]


def test_empty_keywords_rejected() -> None:
    with pytest.raises(ValueError):
        Prefilter([])

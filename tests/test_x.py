"""X (Twitter) search-result parsing: profiles only, founder + company from the bio; x.com is never fetched."""

from screener.scouts.x import is_x, parse_x_profile, profile_handle
from screener.urls import is_blocked_by_policy
from screener.verify.rules import _non_linkedin


def test_profile_handle_only_for_profile_urls() -> None:
    assert profile_handle("https://x.com/vamsi_edu") == "vamsi_edu"
    assert profile_handle("https://twitter.com/asharao?lang=en") == "asharao"
    assert profile_handle("https://x.com/TheDailyPioneer/status/2034977506684772357") is None  # a post
    assert profile_handle("https://x.com/search?q=edtech") is None
    assert profile_handle("https://example.com/vamsi") is None
    assert is_x("https://mobile.twitter.com/a") and not is_x("https://xcom.in/a")


def test_parse_profile_founder_and_company_from_bio() -> None:
    p = parse_x_profile("Asha Rao (@asharao) / X", "Co-founder & CEO @AcmeLearning | ex-Byju's | IIT Delhi")
    assert p == {"name": "Asha Rao", "handle": "asharao", "company": "AcmeLearning",
                 "bio": "Co-founder & CEO @AcmeLearning | ex-Byju's | IIT Delhi"}
    assert parse_x_profile("Vamsi Krishna (@vamsi_edu) / X", "Founder, Vedantu Learning. Teacher.")["company"] == "Vedantu Learning"
    assert parse_x_profile("Deepti Sawhney (@deeptisawhney) / X", "Educator and speaker")["company"] is None
    assert parse_x_profile("The Pioneer (@TheDailyPioneer) / X", "")["company"] is None
    assert parse_x_profile("Mahesh Wagh (@mw) / X", "Co-founder, edtech startup for engineers")["company"] is None
    assert parse_x_profile("Daniel Bright (@db) / X", "Founder of an edtech platform")["company"] is None
    assert parse_x_profile("edtechnews (@edtechnews) / X", "Founder of nothing") is None  # not a person's name


def test_x_is_never_fetched_and_never_verifies_identity_alone() -> None:
    assert is_blocked_by_policy("https://x.com/asharao") and is_blocked_by_policy("https://twitter.com/a")
    assert not _non_linkedin("https://x.com/asharao") and not _non_linkedin("https://in.linkedin.com/in/a")
    assert _non_linkedin("https://inc42.com/story")

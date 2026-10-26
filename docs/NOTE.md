# Founder Screener for upGrad: how it works, what we found, how we checked it

A working screener that finds education founders and companies (India first), checks every fact against a
source, scores them in code and explains every pick. It ran end to end on real data on 25 Oct 2026.

## 1. Criteria (every one is a fact we can check)

**Strong track record** (per founder; the strongest co-founder counts): prior exit (25%), prior founder of a
VC-backed company (20%), top-tier or edtech-specialist investor (15%), operating scale such as revenue (15%), years
building in education (10%), senior role at a scaled edtech (10%), technical depth or accelerator signal (5%).

**Interesting** (per company): momentum (recent rounds, open roles, fresh news, GitHub activity, decaying with a
180-day half-life) and novelty (distance from crowded clusters of similar companies, and funding growth in its niche).

**Fit for upGrad**, three lenses, the best names the fit type: **Acquire** (fills a capability upGrad wants, below
the size ceiling), **Partner** (complements an existing upGrad business), **Co-build** (very early: under $3M raised,
seed stage or at most 3 years old). **India first**: verified Indian location x1.0, unverified x0.85, outside x0.6,
and at least 10 of 15 picks are Indian when the pool allows.

**Gates**: an education business; not inactive; not acquired; not a scaled competitor or upGrad's own; at most $100M
raised and 1,000 people; founder identity confirmed by a non-social source; at least 3 verified track-record facts.

**Where we look**: news feeds and GDELT, accelerator and VC portfolio pages, curated lists, GitHub, Hacker News,
BetaList, the YC directory, company websites, Tofler/Zauba, LinkedIn and X through search results only, and the
Ministry of Corporate Affairs company master (data.gov.in): all 26.5 lakh active companies scanned, 1.46 lakh
education candidates kept (education industry code, or education words in the name, since most edtechs register
as IT companies).

## 2. How the screener works

Seven resumable stages: **discover** (11 scouts) → **resolve** (merge mentions into companies and founders) →
**enrich** (company sites, news, registries, founder interviews; targeted lookups for missing size, location,
founder background and X) → **verify** → **score** → **explain** → **publish**.

- Every fact is a **claim**: statement, source URL, exact quote, time and confidence. No source, no fact.
- **The AI extracts, checks and explains; code scores.** Score = T^0.3 x I^0.3 x F^0.3 x C^0.1 (C = share of facts
  verified), with small-evidence shrinkage. The top 60 are compared in pairs by Gemini in both orders (cancelling
  position bias), turned into a Bradley-Terry ranking and blended 70/30 with the score; 15 are picked for score and
  variety (MMR).
- **LinkedIn and X**: never scraped or logged into. We read search results ("Name x.com", "site:linkedin.com/in
  founder edtech") and keep profile URLs as identity links; a social profile never verifies a founder on its own.
- **Registry at scale**: Gemini triages registry rows by name and registry fields (routing only, never stored as a
  fact; 44,800 of 1.25 lakh done so far: 8,547 likely edtech products), then the likeliest are checked against
  their own website (3 parallel workers, ~6,000 an hour) and education businesses are enriched and verified like
  every other company (153 confirmed so far). The sweep paused when the Gemini project hit its spend cap.
- **Dashboard**: live runs, the shortlist with founder profiles and sources, every left-out company with its reason,
  India / Global filter, and a search box (typed or spoken) that answers from verified data first and from cited
  Gemini web research second, clearly labelled as unverified.
- **AI Policy**: the team edits categories (Acquire / Partner / Co-build, or their own), 20 filters and the weights
  in a structured form, previews the shortlist change before saving, then re-scores. Every version is kept and
  can be rolled back.

## 3. What we found

**Shortlist (15)**: Arivihan, Toprankers, byteXL, SuperKalam, Nprep, Emversity, AlgoUniversity, AccioJob, AppX,
YoLearn.ai (India); Perfectum AI, Ahura AI, Issen, Genially, Golpo (Global view). Fit types: 7 Acquire, 4 Partner,
4 Co-build. Each has a cited why-line, a fit-line, founder lines and three source links.

**Who we left out, and why**
- **Leap Scholar** (already a scaled player): raised $150M, above the $100M ceiling; study abroad and test prep.
- **College Circle AI** (not enough verified evidence): AI tutoring, but fewer than 3 verified track-record facts.
- **NxtWave** (a close call): ranked #14 but too similar to byteXL, which serves the same need.

## 4. How we checked the output is accurate

1. **Grounding**: every extracted quote must appear on the fetched page (fuzzy match >= 90, numbers exact).
2. **Independent verifier** on a fresh fetch, seeing only the plain statement and the page: 3,281 claims supported,
   470 not found, 46 contradicted.
3. **Corroboration**: funding, revenue and exits need two independent domains or one primary source.
4. **Explanations checked in code**: every sentence cites real claims and contains no number not in them.
5. **Manual audit** of 30 random shortlist claims (docs/audit/claims-sample.csv), each source opened and read:
   **26 correct facts (87%)**, 23 with a quote that clearly states them, 3 correct but weakly evidenced, 1 unclear,
   **3 wrong, all one error**: the website of a Moroccan namesake ("101edu Maroc") was attached to the US 101edu.
   The audit also showed that 101edu had been acquired. We fixed the data, added an ACQUIRED gate (101edu is now
   excluded) and stopped treating Gemini search summaries as quotable sources.
6. **Hand review of the top 15**: names, founders and source links opened; one duplicate founder record fixed.
7. **Stability**: every weight shaken by ±20% in 20 trials: Kendall tau 0.90 on average (min 0.79), and the same 15
   companies every time. Recall: 4 of 4 seed positives in the top 50.

## 5. What did not work (and what we did)

- **LinkedIn and X block scraping**; X's robots.txt also blocks its embed endpoint. Snippets work, but few X bios
  name the company, so an X profile is attached only when the bio names it.
- **Free search engines fail at volume** (bot blocks, empty results on long queries); Gemini's Google Search
  grounding replaced them for registry website discovery.
- **First scoring version let wrong names through**: unknown funding let a large US company pass (now penalised and
  flagged), an old news date marked active Indian companies inactive (inactive now needs combined evidence), co-build
  was never chosen (a tie always went to acquire), name-order variants created duplicate founders (now merged).
- **Name collisions**: a website search can find a namesake company (the 101edu case). Team pages can list staff as
  founders. Both need a cross-check against registry data (CIN, directors), which we would add next.
- **Location is often missing**: we infer India only from verified evidence and show which evidence decided it.

## 6. With another week

1. **Finish the registry**: website-check every likely edtech among the 1.46 lakh (~6,000 an hour with 3 workers,
   within a planned Gemini budget),
   add DPIIT recognised startups, and cross-check founders against MCA directors to stop name collisions.
2. **Founder graph**: link founders to past companies and co-founders to score exits and pedigree more often.
3. **Learn weights from reviewer labels** synced back from the Sheet; measure recall on a reviewer-built seed list.
4. **Paid data where it pays off**: a people-data API for founder histories and an X API tier, under the same rules.
5. **Host it** with a daily scheduled run and alerts when a tracked company raises, launches or starts hiring.

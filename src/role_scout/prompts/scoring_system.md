# Job Relevance Scoring System

You are scoring PM job listings for a specific candidate. Your output must be a valid JSON array — no markdown, no explanation, just the raw JSON array.

## Candidate Profile

- **Name:** $name
- **Target titles:** $target_roles
- **Seniority level:** $seniority_level
- **Preferred domains:** $preferred_domains
- **Location:** $location (remote_ok: $remote_ok)
- **Target company stages:** $target_stages
- **Minimum comp:** $${comp_min_k}K
- **Key skills:** $skills
- **Must-have keywords:** $must_have_keywords
- **Anti-keywords (automatic disqualifier):** $anti_keywords

## Scoring Rubric

### Seniority Match — 30 points
- **28–30:** Exact title match (Senior PM, Staff PM, Principal PM, or equivalent)
- **20–27:** One level adjacent (e.g. PM II, Group PM, Lead PM)
- **10–19:** Significant stretch or step down (Manager of PM, Director required, or junior-leaning)
- **0–9:** Completely mismatched level (VP, CPO, APM, IC Engineer)
- **Automatic 0:** Title contains any anti-keyword (Director, VP, Head of Product, CPO, APM)

### Domain Alignment — 25 points
- **23–25:** Primary target domain exactly (AI/ML, GenAI, Agentic AI, LLM, Platform, Developer Tools)
- **15–22:** Adjacent domain with strong transferable context (API-first, Cloud Infra, Data Platform, Enterprise SaaS)
- **5–14:** Tangential domain — some overlap but not a primary fit
- **0–4:** Unrelated domain (B2C consumer, gaming, hardware, biotech)

### Location Fit — 20 points
- **20:** Fully remote (any location) — always fits
- **17–19:** Bay Area hybrid or Bay Area onsite
- **10–16:** Non-Bay-Area city, hybrid, with explicit remote-ok language
- **5–9:** Non-Bay-Area city, onsite, no remote-ok language but relocation possible
- **0–4:** Requires relocation to a city with no remote option stated

### Company Stage Fit — 15 points
- **13–15:** Series B, C, or D — sweet spot for this candidate
- **8–12:** Series A or late Series D / Growth equity
- **4–7:** Pre-IPO, large company >5000 employees, or early-stage (<Series A)
- **0–3:** Public company (post-IPO) or seed/pre-seed

### Comp Transparency — 10 points

> **BEFORE SCORING ANY JOB:** If `salary_visible` is `false`, comp_score is **always 5**. Skip the rubric below for that job's comp dimension — it does not apply. Only use the rubric when `salary_visible` is `true`.

- **8–10:** `salary_visible: true` AND lower bound >= $${comp_min_k}K
- **2–4:** `salary_visible: true` AND lower bound between $$150K–$${comp_min_k_minus_1}K
- **0–1:** `salary_visible: true` AND lower bound significantly below $$150K
- **5 (neutral):** `salary_visible: false` — comp data absent, no penalty, no reward. **This is not a low score. It is a neutral midpoint.**

## Critical Rules

1. **match_pct MUST equal** seniority_score + domain_score + location_score + stage_score + comp_score exactly.
2. **`salary_visible: false` → comp_score = 5. Always. No exceptions.** Returning 0 for a job with no listed salary is a scoring error.
3. Anti-keywords in the title → seniority_score = 0.
4. Scores must be integers, not floats.
5. Return **exactly $n objects** in the same order as the input jobs.
6. **Self-check before returning:** scan your output — any job where `salary_visible` is `false` must have `comp_score: 5`. Correct it if not.

## Output Format

Return a JSON array with exactly $n objects. Each object must have these keys:
hash_id (string, copy from input), match_pct (integer), seniority_score (integer),
domain_score (integer), location_score (integer), stage_score (integer),
comp_score (integer), reasoning (2-3 sentences), key_requirements (3-5 strings),
red_flags (0-3 strings), domain_alignment (string), seniority_match (string),
location_fit (string), company_stage_fit (string).

## Jobs to Score

$jobs_json

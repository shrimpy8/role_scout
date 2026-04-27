"""normalize_jobs(): map raw source dicts to NormalizedJob models."""

import re

from bs4 import BeautifulSoup
from pydantic import ValidationError

from role_scout.compat.logging import get_logger
from role_scout.compat.models import NormalizedJob
from role_scout.compat.sources import SOURCES

logger = get_logger(__name__)

_COMP_RE = re.compile(r"\$?\s*([\d,]+)\s*[Kk]?")

_FIELD_MAPS: dict[str, dict[str, list[str]]] = {
    "linkedin": {},
    "google_jobs": {
        "title":   ["title"],
        "company": ["company_name", "company"],
    },
    "trueup": {
        "title":         ["title"],
        "company":       ["company"],
        "location":      ["location"],
        "url":           ["url"],
        "posted_date":   ["postedAt"],
        "description":   ["description"],
        "comp_raw":      ["salary", "comp"],
        "company_stage": ["companyStage"],
    },
}


def _strip_html(text: str) -> str:
    """Strip HTML tags from text. No-op if text contains no '<'."""
    if not text or "<" not in text:
        return text
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


def _extract_field(raw: dict, keys: list[str]) -> str:
    """Return the first non-empty string found in *raw* for any of *keys*."""
    for key in keys:
        val = raw.get(key)
        if val:
            return str(val)
    return ""


_ATS_DOMAINS = frozenset({
    "lever.co", "greenhouse.io", "ashbyhq.com", "myworkdayjobs.com",
    "smartrecruiters.com", "bamboohr.com", "icims.com", "jobvite.com",
    "taleo.net", "applytojob.com", "recruitee.com", "workable.com",
    "jobs.lever.co", "boards.greenhouse.io", "apply.workable.com",
})
_JOB_BOARD_DOMAINS = frozenset({
    "indeed.com", "whatjobs.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "simplyhired.com", "jooble.org", "bebee.com",
    "careerbuilder.com", "linkedin.com/jobs",
})


def _is_direct_url(url: str) -> bool:
    """Return True if url points to an ATS or company-hosted page, not an aggregator."""
    return any(d in url for d in _ATS_DOMAINS) or not any(d in url for d in _JOB_BOARD_DOMAINS)


def _best_apply_url(apply_options: list[dict]) -> str | None:
    """Scan apply_options and return the best direct ATS URL, falling back to first entry."""
    if not apply_options:
        return None
    for opt in apply_options:
        link = opt.get("link", "")
        if link and any(d in link for d in _ATS_DOMAINS):
            return link
    return apply_options[0].get("link") or None


_GENERIC_LOCATION_TOKENS = {"remote", "anywhere", "united states", "us", "usa", "united kingdom", "uk", "worldwide", "global"}

_US_STATE_ABBRS = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})
_US_STATE_NAMES = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "district of columbia",
})
_US_TOKENS = frozenset({"us", "usa", "united states", "america"})
_UK_TOKENS = frozenset({"uk", "united kingdom", "england", "scotland", "wales"})


def _normalize_loc_case(s: str) -> str:
    """Apply title case when a location string is uniformly ALL-CAPS or all-lower."""
    if not s:
        return s
    if s == s.upper() or s == s.lower():
        return s.title()
    return s


def _extract_city(location: str) -> str:
    """Extract city from location string, stripping work_model parentheticals."""
    if not location:
        return ""
    clean = re.sub(r"\s*\([^)]*\)", "", location).strip()
    clean = re.split(r"[;/]", clean)[0].strip()
    if " - " in clean:
        before_dash, after_dash = clean.split(" - ", 1)
        if "," not in before_dash:
            after_dash = after_dash.strip()
            if after_dash.lower() in _GENERIC_LOCATION_TOKENS:
                return ""
            clean = after_dash
    city = clean.split(",")[0].strip()
    if city.lower() in _GENERIC_LOCATION_TOKENS:
        return ""
    return _normalize_loc_case(city)


def _extract_country(location: str) -> str:
    """Return country code (US / UK / '') inferred from location string."""
    if not location:
        return ""
    lower = location.lower()
    for token in _UK_TOKENS:
        if token in lower:
            return "UK"
    for token in _US_TOKENS:
        if token in lower:
            return "US"
    for state in _US_STATE_NAMES:
        if state in lower:
            return "US"
    for part in re.split(r"[\s,\-/;()]+", location):
        if part.upper() in _US_STATE_ABBRS:
            return "US"
    return ""


_COMP_SUFFIX_RE = re.compile(
    r"\s+(a|per)\s+(year|yr|month|mo|hour|hr|week|wk)\s*$"
    r"|\s+annually\s*$"
    r"|\s+/\s*(year|yr|month|mo)\s*$",
    re.IGNORECASE,
)
_COMP_CURRENCY_RE = re.compile(r"\bUSD\b|\$", re.IGNORECASE)


def _normalize_comp_display(raw: str | None) -> str | None:
    """Normalize raw comp string to a consistent short range (e.g. '175K-220K')."""
    if not raw:
        return raw
    s = raw.strip()
    s = _COMP_SUFFIX_RE.sub("", s).strip()
    s = _COMP_CURRENCY_RE.sub("", s).strip()
    s = re.sub(r"[–—]", "-", s)
    s = re.sub(r"\s*-\s*", "-", s)

    def _to_k(m: re.Match) -> str:
        num_str = m.group(0).replace(",", "")
        try:
            num = int(num_str)
            return f"{num // 1000}K" if num >= 10_000 else m.group(0)
        except ValueError:
            return m.group(0)

    s = re.sub(r"\d[\d,]*", _to_k, s)
    s = re.sub(r"\s+", " ", s).strip(" -,")
    return s or None


def _parse_comp(raw: str | None, comp_min_k: int) -> tuple[str | None, bool]:
    """Return (comp_range, salary_visible)."""
    if not raw:
        return None, False
    raw = raw.strip()
    matches = _COMP_RE.findall(raw)
    if not matches:
        return raw, False
    lower_str = matches[0].replace(",", "")
    multiplier = 1000 if re.search(r"[\d,]+\s*[Kk]", raw) else 1
    try:
        lower = int(lower_str) * multiplier
    except ValueError:
        return raw, False
    visible = lower >= comp_min_k * 1000
    return raw, visible


def _infer_work_model(raw: str) -> str:
    """Infer work_model string from free-text location or work-type field."""
    if not raw:
        return "unknown"
    lower = raw.lower()
    if "remote" in lower or "anywhere" in lower:
        return "remote"
    if "hybrid" in lower:
        return "hybrid"
    if any(x in lower for x in ("on-site", "onsite", "in-office", "in office")):
        return "onsite"
    return "unknown"


def _from_linkedin(raw: dict, comp_min_k: int) -> dict | None:
    """Map a raw LinkedIn (Apify harvestapi) job dict to normalized field dict."""
    title = raw.get("title", "")
    company_obj = raw.get("company") or {}
    company = company_obj.get("name", "") if isinstance(company_obj, dict) else str(company_obj)
    if not title or not company:
        return None
    location_obj = raw.get("location") or {}
    if isinstance(location_obj, dict):
        location = (location_obj.get("linkedinText")
                    or location_obj.get("parsed", {}).get("city", "")
                    or "")
    else:
        location = str(location_obj)
    if location.lower().strip() in _GENERIC_LOCATION_TOKENS:
        location = ""
    apply_method = raw.get("applyMethod") or {}
    company_apply_url = apply_method.get("companyApplyUrl") or None
    linkedin_url = raw.get("linkedinUrl") or ""
    url = (company_apply_url
           or raw.get("easyApplyUrl")
           or linkedin_url)
    salary_obj = raw.get("salary") or {}
    comp_raw = salary_obj.get("text") if isinstance(salary_obj, dict) else None
    comp_range, salary_visible = _parse_comp(comp_raw, comp_min_k)
    comp_range = _normalize_comp_display(comp_range)
    work_raw = raw.get("workplaceType") or raw.get("employmentType") or ""
    description = raw.get("descriptionText") or raw.get("descriptionHtml") or ""
    return {
        "title": title.strip(),
        "company": company.strip(),
        "location": location,
        "city": _extract_city(location),
        "country": _extract_country(location),
        "work_model": _infer_work_model(work_raw),
        "url": url,
        "apply_url": company_apply_url or (linkedin_url or None),
        "source": "linkedin",
        "posted_date": raw.get("postedDate"),
        "description": _strip_html(str(description))[:2000],
        "comp_range": comp_range,
        "salary_visible": salary_visible,
        "company_stage": None,
    }


def _from_google_jobs(raw: dict, comp_min_k: int) -> dict | None:
    """Map a raw Google Jobs (SerpAPI) job dict to normalized field dict."""
    fmap = _FIELD_MAPS["google_jobs"]
    title = _extract_field(raw, fmap["title"])
    company = _extract_field(raw, fmap["company"])
    if not title or not company:
        return None
    location = _normalize_loc_case(raw.get("location") or "")
    ext = raw.get("detected_extensions") or {}
    description = raw.get("description") or raw.get("snippet", "")
    comp_raw = ext.get("salary")
    comp_range, salary_visible = _parse_comp(comp_raw, comp_min_k)
    comp_range = _normalize_comp_display(comp_range)
    apply_opts = raw.get("apply_options") or []
    url = raw.get("source_link", "") or (apply_opts[0].get("link", "") if apply_opts else "")
    apply_url = _best_apply_url(apply_opts) or (url if _is_direct_url(url) else None)
    if ext.get("work_from_home"):
        work_model = "remote"
    else:
        work_model = _infer_work_model(str(ext.get("schedule_type") or ""))
        if work_model == "unknown":
            work_model = _infer_work_model(location)
    return {
        "title": title.strip(),
        "company": company.strip(),
        "location": location,
        "city": _extract_city(location),
        "country": _extract_country(location),
        "work_model": work_model,
        "url": url,
        "apply_url": apply_url,
        "source": "google_jobs",
        "posted_date": ext.get("posted_at"),
        "description": _strip_html(str(description))[:2000],
        "comp_range": comp_range,
        "salary_visible": salary_visible,
        "company_stage": None,
    }


def _from_trueup(raw: dict, comp_min_k: int) -> dict | None:
    """Map a raw TrueUp (IMAP-parsed) job dict to normalized field dict."""
    fmap = _FIELD_MAPS["trueup"]
    title = _extract_field(raw, fmap["title"])
    company = _extract_field(raw, fmap["company"])
    if not title or not company:
        return None
    location = _normalize_loc_case(_extract_field(raw, fmap["location"]) or "Remote")
    comp_range, salary_visible = _parse_comp(_extract_field(raw, fmap["comp_raw"]) or None, comp_min_k)
    comp_range = _normalize_comp_display(comp_range)
    return {
        "title": title.strip(),
        "company": company.strip(),
        "location": location,
        "city": _extract_city(location),
        "country": _extract_country(location),
        "work_model": _infer_work_model(location),
        "url": _extract_field(raw, fmap["url"]),
        "apply_url": _extract_field(raw, fmap["url"]) or None,
        "source": "trueup",
        "posted_date": _extract_field(raw, fmap["posted_date"]) or None,
        "description": _strip_html(_extract_field(raw, fmap["description"]))[:2000] or None,
        "comp_range": comp_range,
        "salary_visible": salary_visible,
        "company_stage": _extract_field(raw, fmap["company_stage"]) or None,
    }


_EXTRACTORS = {
    "linkedin":    _from_linkedin,
    "google_jobs": _from_google_jobs,
    "trueup":      _from_trueup,
}
assert set(_EXTRACTORS) == set(SOURCES), (
    f"_EXTRACTORS keys {set(_EXTRACTORS)} don't match SOURCES {set(SOURCES)}"
)


def normalize_jobs(
    raw_jobs: list[dict],
    source: str,
    comp_min_k: int = 175,
) -> list[NormalizedJob]:
    """Map raw source dicts to NormalizedJob models, skipping invalid entries."""
    extractor = _EXTRACTORS.get(source)
    if extractor is None:
        logger.error("normalize_unknown_source", source=source)
        return []

    result: list[NormalizedJob] = []
    skipped = 0

    for raw in raw_jobs:
        try:
            fields = extractor(raw, comp_min_k)
        except Exception:
            logger.exception("normalize_extractor_error", source=source)
            skipped += 1
            continue

        if fields is None:
            logger.debug("normalize_skipped_missing_fields", source=source)
            skipped += 1
            continue

        if not fields.get("url"):
            logger.debug("normalize_skipped_no_url", source=source, title=fields.get("title"))
            skipped += 1
            continue

        try:
            job = NormalizedJob(**fields)
            result.append(job)
        except ValidationError:
            logger.exception("normalize_validation_error", source=source, title=fields.get("title"))
            skipped += 1

    logger.info("normalize_complete", source=source, normalized=len(result), skipped=skipped)
    return result

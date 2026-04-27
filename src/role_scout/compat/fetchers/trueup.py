"""TrueUp job digest fetcher — connects to IMAP, parses weekly HTML emails."""

import email
import imaplib
import re
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup, Tag

from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

_TRACKING_DOMAINS = {"url3500.trueup.io", "trueup.io"}


def _is_tracking_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in _TRACKING_DOMAINS)
    except Exception:
        return False


def _resolve_trueup_urls(jobs: list[dict], timeout: int = 5) -> None:
    """Resolve Mimecast/TrueUp tracking URLs to final job posting URLs in-place."""
    tracking = [(i, job) for i, job in enumerate(jobs) if _is_tracking_url(job.get("url", ""))]
    if not tracking:
        return

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            for i, job in tracking:
                try:
                    resp = client.head(job["url"])
                    resp.raise_for_status()
                    final_url = str(resp.url)
                    if not _is_tracking_url(final_url):
                        jobs[i]["url"] = final_url
                        logger.debug("trueup_url_resolved", final=final_url)
                    else:
                        logger.warning("trueup_url_still_tracking", url=job["url"])
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        logger.warning("trueup_tracking_url_expired", url=job["url"])
                    else:
                        logger.warning("trueup_url_resolve_failed", url=job["url"], status=exc.response.status_code)
                except (httpx.TimeoutException, httpx.RequestError):
                    logger.warning("trueup_url_resolve_timeout", url=job["url"])
    except (OSError, httpx.TransportError):
        logger.exception("trueup_url_resolve_error")

_SUBJECT_FILTER = "new jobs for you this week"


def _decode_body(msg: email.message.Message) -> str | None:
    """Extract the HTML part from a multipart email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return None


def _parse_posted_date(text: str) -> str | None:
    """Convert TrueUp relative date text to ISO date string."""
    text = text.strip().lower()
    today = date.today()
    if text == "today":
        return today.isoformat()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = re.match(r"(\d+)\s+days?\s+ago", text)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return text if text else None


def _extract_stage(metadata_div: Tag) -> str | None:
    """Extract company stage from the metadata div below the job card table."""
    text = metadata_div.get_text(" ", strip=True)
    if "unicorn" in text.lower():
        return "Unicorn"
    if "early-stage" in text.lower() or "early stage" in text.lower():
        return "Early-stage"
    if "series d" in text.lower():
        return "Series D"
    if "series c" in text.lower():
        return "Series C"
    if "series b" in text.lower():
        return "Series B"
    if "series a" in text.lower():
        return "Series A"
    if "seed" in text.lower():
        return "Seed"
    if "public" in text.lower():
        return "Public"
    return None


def _parse_job_card(card: Tag) -> dict | None:
    """Parse one TrueUp job card div into a raw job dict."""
    tds = card.find_all("td")
    content_td = None
    for td in tds:
        style = td.get("style", "")
        if "width:48" not in style and "width: 48" not in style:
            content_td = td
            break
    if content_td is None:
        return None

    child_divs = [d for d in content_td.children if isinstance(d, Tag) and d.name == "div"]
    if len(child_divs) < 3:
        return None

    title_div = child_divs[0]
    title_a = title_div.find("a")
    if not title_a:
        return None
    title = title_a.get_text(strip=True)
    url = title_a.get("href", "")

    company_div = child_divs[1]
    company_a = company_div.find("a")
    company = company_a.get_text(strip=True) if company_a else company_div.get_text(strip=True).split("\n")[0].strip()

    location = child_divs[2].get_text(strip=True)

    posted_at = None
    if len(child_divs) >= 4:
        posted_at = _parse_posted_date(child_divs[3].get_text(strip=True))

    metadata_div = card.find(
        "div",
        style=lambda s: s and "border-top" in s and "d7d2d2" in s,
    )
    company_stage = _extract_stage(metadata_div) if metadata_div else None

    if not title or not company or not url:
        return None

    return {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "postedAt": posted_at,
        "description": None,
        "salary": None,
        "companyStage": company_stage,
    }


def _parse_email_html(html: str) -> list[dict]:
    """Parse job cards from one TrueUp digest HTML body."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []

    for div in soup.find_all("div", style=True):
        style = div.get("style", "")
        if "border:1px solid #ddd" in style and "border-radius:10px" in style:
            job = _parse_job_card(div)
            if job:
                jobs.append(job)

    return jobs


def fetch_trueup(
    user: str,
    password: str,
    host: str = "imap.mail.yahoo.com",
    folder: str = "TrueUp",
    max_emails: int = 3,
) -> list[dict]:
    """Fetch jobs from TrueUp weekly digest emails via IMAP."""
    if not user or not password:
        logger.warning("trueup_imap_not_configured")
        return []

    try:
        conn = imaplib.IMAP4_SSL(host)
    except OSError:
        logger.exception("trueup_imap_connect_failed", host=host)
        return []

    try:
        conn.login(user, password)
    except imaplib.IMAP4.error:
        logger.exception("trueup_imap_login_failed", user=user)
        conn.logout()
        return []

    try:
        conn.select(folder, readonly=False)
        _typ, data = conn.search(None, f'SUBJECT "{_SUBJECT_FILTER}"')
        if _typ != "OK" or not data or not data[0]:
            logger.info("trueup_no_emails_found", folder=folder)
            return []

        msg_ids = data[0].split()[-max_emails:]
        logger.info("trueup_emails_found", count=len(msg_ids))

        all_jobs: list[dict] = []
        for msg_id in msg_ids:
            _typ, msg_data = conn.fetch(msg_id, "(RFC822)")
            if _typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            html = _decode_body(msg)
            if not html:
                logger.warning("trueup_no_html_body", msg_id=msg_id)
                continue
            jobs = _parse_email_html(html)
            _resolve_trueup_urls(jobs)
            logger.info("trueup_parsed_email", msg_id=msg_id.decode(), jobs=len(jobs))
            all_jobs.extend(jobs)
            conn.store(msg_id, "+FLAGS", "\\Seen")

        return all_jobs

    except Exception:
        logger.exception("trueup_fetch_error")
        return []
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass

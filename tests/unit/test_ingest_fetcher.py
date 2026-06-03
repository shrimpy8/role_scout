"""Unit tests for the ingest fetcher module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from role_scout.ingest.fetcher import FetchResult, _extract_text, fetch_url

# ---------------------------------------------------------------------------
# _extract_text tests
# ---------------------------------------------------------------------------

def test_extract_text_finds_job_description_class():
    html = """<html><body>
    <nav>Nav stuff</nav>
    <div class="job-description"><p>We are looking for a Senior Engineer to join our team.</p>
    <p>Requirements: Python, AWS, 5+ years experience. You will work on exciting distributed systems
    with a talented team. We offer competitive salary, flexible hours, and great benefits. Apply now!</p>
    </div>
    <footer>Footer</footer></body></html>"""
    text = _extract_text(html)
    assert "Senior Engineer" in text
    assert "Nav stuff" not in text
    assert "Footer" not in text


def test_extract_text_falls_back_to_paragraphs():
    html = """<html><body>
    <p>We are hiring a Data Engineer to build our data platform.</p>
    <p>You will work with Spark, Kafka, and Python to process millions of events daily.</p>
    <p>We offer remote work, equity, and great benefits for this exciting role.</p>
    </body></html>"""
    text = _extract_text(html)
    assert "Data Engineer" in text
    assert "Spark" in text


def test_extract_text_strips_script_and_style():
    html = """<html><head><style>body { font: 12px; }</style></head>
    <body>
    <script>alert('hello')</script>
    <main><p>Join our team as a Product Manager. You will own the roadmap for our core product.</p>
    <p>Requirements: 5+ years PM experience, data-driven mindset, strong communication skills.</p></main>
    </body></html>"""
    text = _extract_text(html)
    assert "Product Manager" in text
    assert "alert" not in text
    assert "font:" not in text


def test_extract_text_truncates_to_max():
    long_text = "x " * 3000
    html = f"<html><body><main><p>{long_text}</p></main></body></html>"
    text = _extract_text(html)
    assert len(text) <= 4000


def test_extract_text_returns_empty_for_no_content():
    html = "<html><body></body></html>"
    text = _extract_text(html)
    assert text == ""


# ---------------------------------------------------------------------------
# fetch_url tests
# ---------------------------------------------------------------------------

_GOOD_HTML = """<html><body>
<div class="job-description">
<p>Senior Software Engineer at ExampleCorp. We are looking for a talented engineer to join our growing team.</p>
<p>Requirements: Python, Go, Kubernetes. 5+ years of backend experience required in distributed systems.</p>
<p>You will design and build distributed systems serving millions of users across multiple data centers.</p>
<p>Location: San Francisco, CA. Hybrid work model with two days in office per week. Salary: $180K-$220K plus equity.</p>
<p>About us: ExampleCorp is a leading provider of cloud infrastructure solutions serving Fortune 500 clients.</p>
<p>Apply by submitting your resume and cover letter at careers.example.com/senior-swe before the deadline.</p>
</div>
</body></html>"""


def _make_mock_response(status_code: int = 200, content: str = _GOOD_HTML):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = content
    # is_redirect must be a plain bool so the redirect-following loop terminates.
    mock_resp.is_redirect = False
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_resp
        )
    return mock_resp


@patch("role_scout.ingest.fetcher.httpx.Client")
def test_fetch_url_ok(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.return_value = _make_mock_response(200, _GOOD_HTML)

    result = fetch_url("https://example.com/jobs/123")
    assert result.status == "ok"
    assert "Senior Software Engineer" in result.raw_text
    assert len(result.raw_text) >= 300


@patch("role_scout.ingest.fetcher.httpx.Client")
def test_fetch_url_thin_content(mock_client_cls):
    thin_html = "<html><body><main><p>Apply here.</p></main></body></html>"
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.return_value = _make_mock_response(200, thin_html)

    result = fetch_url("https://example.com/jobs/js-heavy")
    assert result.status == "thin"


@patch("role_scout.ingest.fetcher.httpx.Client")
def test_fetch_url_timeout(mock_client_cls):
    import httpx
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.side_effect = httpx.TimeoutException("timeout")

    result = fetch_url("https://example.com/jobs/slow")
    assert result.status == "failed"
    assert result.error is not None
    assert "Timeout" in result.error


@patch("role_scout.ingest.fetcher.httpx.Client")
def test_fetch_url_http_404(mock_client_cls):
    import httpx
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_resp = _make_mock_response(404, "Not found")
    mock_client.get.return_value = mock_resp

    result = fetch_url("https://example.com/jobs/missing")
    assert result.status == "failed"
    assert "404" in (result.error or "")


@patch("role_scout.ingest.fetcher.httpx.Client")
def test_fetch_url_greenhouse_selector(mock_client_cls):
    greenhouse_html = """<html><body>
    <div class="job__description">
    <p>We are looking for an experienced Staff Engineer to join our infrastructure team at GreenhouseCo.</p>
    <p>You will lead the design of our distributed systems and mentor a team of junior and mid-level engineers.</p>
    <p>Requirements: 8+ years experience, distributed systems expertise, proficiency in Python or Go required.</p>
    <p>Compensation: $220K-$280K + equity. Remote-first culture with quarterly in-person offsites at HQ.</p>
    <p>You will own the full technical roadmap for the platform and present quarterly to executive leadership.</p>
    <p>Apply via our careers page at greenhouse.io or reach out directly to our recruiting team today.</p>
    </div>
    </body></html>"""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.return_value = _make_mock_response(200, greenhouse_html)

    result = fetch_url("https://boards.greenhouse.io/company/jobs/123456")
    assert result.status == "ok"
    assert "Staff Engineer" in result.raw_text


# ---------------------------------------------------------------------------
# Integration test corpus (skipped unless INTEGRATION_TESTS=1)
# ---------------------------------------------------------------------------

REAL_URLS = [
    "https://boards.greenhouse.io/abridge/jobs/6268563",
    "https://www.builtinnyc.com/job/software-engineer/",
    "https://jobs.ashbyhq.com/anthropic/senior-software-engineer",
    "https://www.ziprecruiter.com/jobs/search?search=data+engineer",
    "https://jobs.snowflake.com/jobs/",
]


@pytest.mark.skipif(
    not __import__("os").environ.get("INTEGRATION_TESTS"),
    reason="Set INTEGRATION_TESTS=1 to run real URL fetch tests",
)
@pytest.mark.parametrize("url", REAL_URLS)
def test_fetch_real_url(url: str):
    result = fetch_url(url, timeout_s=20.0)
    # We accept ok or thin — just not a crash
    assert result.status in {"ok", "thin", "failed"}
    assert result.url == url

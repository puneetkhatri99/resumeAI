"""Branded MinistryHub resume: the details AI extracted from a CV (ResumeData) are rendered into
an HTML template (Jinja2) and printed to PDF by headless Chromium (Playwright).

Nothing is stored and CV text is never logged. Every asset (fonts, header and footer images)
is inlined as a data: URI, JavaScript is off and every network request is aborted, because the
page holds AI output.
"""

import base64
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.features.resume_structuring.service import ResumeData

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
ASSETS_DIR = TEMPLATES_DIR / "assets"

PDF_FAILED_MESSAGE = "Could not create the PDF. Try again."
# The header image is 35.6 mm tall at full A4 width and the footer 17.8 mm. Top and sides are
# measured from the sample (content starts 8.4 mm under the header, 13.2 mm from each edge);
# the bottom adds a 6 mm gap. A CSS @page margin would override these, so the template has none.
PDF_MARGIN = {"top": "44mm", "bottom": "23.8mm", "left": "13.2mm", "right": "13.2mm"}
# Exact A4. Playwright's format="A4" is 8.27 x 11.7 in, about 1 pt taller than the sample.
PAGE_SIZE = {"width": "210mm", "height": "297mm"}

ONGOING_WORDS = re.compile(
    r"\b(present|current|currently|now|today|ongoing|till date|to date)\b", re.IGNORECASE
)
# "06/2021", "6-2021" or "06.2021". "1[0-2]" comes first so "10" isn't read as "1".
MONTH_YEAR = re.compile(r"\b(1[0-2]|0?[1-9])[./-]((?:19|20)\d{2})\b")
YEAR_MONTH = re.compile(r"\b((?:19|20)\d{2})-(1[0-2]|0?[1-9])\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# Whole words only, so "Marketing" isn't March and "Decision" isn't December.
MONTH_NAME = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
LINE_BREAK = re.compile(r"\r\n|\r|\n")
# One leading list marker, only when whitespace (or nothing) follows, so "-30%" and "*Important*"
# stay as written.  is Word's Symbol-font bullet as text extraction leaves it.
BULLET_MARKER = re.compile(r"^\s*[•\-*·–▪●◦➢](?:\s+|$)")


class PdfRenderError(Exception):
    """Chromium couldn't render the PDF (HTTP 500)."""


def read_data_uri(filename: str, media_type: str) -> str:
    """Return an asset file as a data: URI, so the page loads it without network or disk access."""
    encoded = base64.b64encode((ASSETS_DIR / filename).read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def read_date(text: str | None, today: date) -> tuple[int, int | None] | None:
    """Read a CV date as written ("Jan 2020", "03/2020", "2020", "Present") into (year, month).

    The month is None when only the year is known. Ongoing words ("Present", "Till date")
    mean today. Returns None when no year can be read.
    """
    if text is None or not text.strip():
        return None
    if ONGOING_WORDS.search(text):
        return today.year, today.month
    if match := MONTH_YEAR.search(text):
        return int(match.group(2)), int(match.group(1))
    if match := YEAR_MONTH.search(text):
        return int(match.group(1)), int(match.group(2))
    year = YEAR.search(text)
    if year is None:
        return None
    month = MONTH_NAME.search(text)
    return int(year.group()), MONTH_NAMES.index(month.group(1)[:3].lower()) + 1 if month else None


def measure_span(start: tuple[int, int | None], end: tuple[int, int | None]) -> tuple[int, bool]:
    """Return (inclusive month count, True) when both months are known (Jan → Oct = 10),
    else (year difference, False). Shared by format_duration and total_years.
    """
    (start_year, start_month), (end_year, end_month) = start, end
    if start_month is None or end_month is None:
        return end_year - start_year, False
    return (end_year - start_year) * 12 + end_month - start_month + 1, True


def format_duration(start: str | None, end: str | None, today: date) -> str | None:
    """Return a job's length as the sample shows it: "10 mos", "1 mo", "1 yr", "11 yrs".

    Under 12 known months counts months; otherwise whole years. None when either date can't
    be read or the span is below 1.
    """
    start_date, end_date = read_date(start, today), read_date(end, today)
    if start_date is None or end_date is None:
        return None
    length, in_months = measure_span(start_date, end_date)
    if length < 1:
        return None
    if in_months and length < 12:
        return "1 mo" if length == 1 else f"{length} mos"
    years = length // 12 if in_months else length
    return "1 yr" if years == 1 else f"{years} yrs"


def total_years(spans: list[tuple[str | None, str | None]], today: date) -> str | None:
    """Return the total work experience ("6 years") from (start_date, end_date) pairs.

    The user's rule: from the earliest readable start to the latest readable end ("Present"
    is today); gaps between jobs count and overlaps count once. Months are used when both
    ends have one (floored to whole years), else the year difference. None (the section is
    hidden) when there is no readable start or end, or the span is under a year.
    """
    starts = [d for d in (read_date(start, today) for start, _ in spans) if d is not None]
    ends = [d for d in (read_date(end, today) for _, end in spans) if d is not None]
    if not starts or not ends:
        return None
    # A start with no month counts as January and an end with no month as December's end.
    earliest = min(starts, key=lambda d: (d[0], d[1] or 0))
    latest = max(ends, key=lambda d: (d[0], d[1] or 13))
    length, in_months = measure_span(earliest, latest)
    years = length // 12 if in_months else length
    if years < 1:
        return None
    return "1 year" if years == 1 else f"{years} years"


def split_bullets(text: str | None) -> list[str]:
    """Split a description on CR/LF into bullet lines, without a leading marker or blank lines."""
    if text is None:
        return []
    lines = (BULLET_MARKER.sub("", line).strip() for line in LINE_BREAK.split(text))
    return [line for line in lines if line]


def drop_blanks(value: Any) -> Any:
    """Strip every string; blank strings become None, and blank list items (strings, or entries
    with nothing in them) are removed, so the page shows no empty tile, pill or "LINK n".
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return [item for item in map(drop_blanks, value) if item]
    if isinstance(value, dict):
        cleaned = {key: drop_blanks(item) for key, item in value.items()}
        return cleaned if any(cleaned.values()) else None
    return value


def is_http_url(url: str | None) -> bool:
    """Whether a URL from AI output may become a link (mirrors linkOrText in the frontend)."""
    return url is not None and url.lower().startswith(("http://", "https://"))


# autoescape=True, not select_autoescape(): that keys on the extension, and ".j2" isn't escaped.
environment = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
environment.filters["bullets"] = split_bullets
environment.tests["http_url"] = is_http_url
environment.globals["format_duration"] = format_duration

FONT_URIS = {
    400: read_data_uri("Inter-Regular.woff2", "font/woff2"),
    500: read_data_uri("Inter-Medium.woff2", "font/woff2"),
    600: read_data_uri("Inter-SemiBold.woff2", "font/woff2"),
}
# Chromium pads its header/footer boxes by 15pt; overriding that lets the images reach the
# paper edge. The template page is the full paper size, so the width is set in percent.
HEADER_TEMPLATE = (
    "<style>#header{padding:0 !important}#header img{display:block;width:100%}</style>"
    f'<img src="{read_data_uri("header.png", "image/png")}" alt="">'
)
FOOTER_TEMPLATE = (
    "<style>#footer{padding:0 !important}#footer img{display:block;width:100%}</style>"
    f'<img src="{read_data_uri("footer.png", "image/png")}" alt="">'
)


def build_resume_html(data: ResumeData, today: date) -> str:
    """Render the branded resume page for `data` as one self-contained HTML string.

    Blank values are dropped first (drop_blanks); links are stripped before the http(s) check.
    """
    cleaned = {key: drop_blanks(value) for key, value in data.model_dump().items()}
    shown = ResumeData.model_validate(
        {**cleaned, "name": data.name.strip(), "email": data.email.strip()}
    )
    spans = [(job.start_date, job.end_date) for job in shown.experience]
    return environment.get_template("resume.html.j2").render(
        data=shown, today=today, total_years=total_years(spans, today), fonts=FONT_URIS
    )


def render_pdf(html: str) -> bytes:
    """Print the page to an A4 PDF with the MinistryHub header and footer on every page.

    Any Playwright failure (Chromium missing, a crash, a timeout) is logged and raised as
    PdfRenderError. Must run in a thread with no running asyncio loop (Playwright's sync API).
    """
    # shortcut: one browser launch per PDF (~1–2 s); keep a shared browser if volume grows
    try:
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page(java_script_enabled=False)
            page.route("**/*", lambda route: route.abort())
            page.set_content(html)
            # page.pdf doesn't wait for web fonts, and Chrome loads @font-face lazily.
            page.evaluate("document.fonts.ready.then(() => true)")
            return page.pdf(
                **PAGE_SIZE,
                print_background=True,
                margin=PDF_MARGIN,
                display_header_footer=True,
                header_template=HEADER_TEMPLATE,
                footer_template=FOOTER_TEMPLATE,
            )
    except PlaywrightError:
        logger.exception("Could not render the PDF")
        raise PdfRenderError(PDF_FAILED_MESSAGE) from None

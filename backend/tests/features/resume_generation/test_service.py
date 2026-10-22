"""Tests for the resume_generation service: date reading, durations, total years, bullets, the
HTML page, and the PDF renderer. Only test_real_render_has_a_header_and_footer_on_every_page
starts Chromium (user-approved); no test calls OpenAI.
"""

import ctypes
import re
from datetime import date
from html.parser import HTMLParser

import playwright.sync_api
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

from app.features.resume_generation import service
from app.features.resume_generation.service import (
    PDF_FAILED_MESSAGE,
    PdfRenderError,
    build_resume_html,
    format_duration,
    read_date,
    render_pdf,
    split_bullets,
    total_years,
)
from app.features.resume_structuring.schemas import (
    Certificate,
    Education,
    Experience,
    Project,
    ResumeData,
)
from tests.conftest import FAKE_RESUME_DATA

TODAY = date(2026, 10, 22)
# Everything that could turn AI text into markup: tags, an event attribute, a closed attribute.
MARKUP_PAYLOAD = (
    '<script>alert(1)</script><img src=x onerror=alert(1)><b>x</b>"><u onmouseover=a()>'
)
HEADINGS = [
    "Professional Summary",
    "Experience & Education",
    "Employment History",
    "Education",
    "Projects",
    "Licenses & Certifications",
    "Skills",
    "Additional Information",
]
# Synthetic candidate with nothing but the required name and email.
MINIMAL_DATA = ResumeData(
    name="Jane Doe",
    email="jane.doe@example.com",
    phone=None,
    location=None,
    links=[],
    skills=[],
    experience=[],
    education=[],
    projects=[],
    certificates=[],
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2021", (2021, None)),
        ("Jan 2026", (2026, 1)),
        ("Sept 2019", (2019, 9)),
        ("September 2019", (2019, 9)),
        ("03/2020", (2020, 3)),
        ("3-2020", (2020, 3)),
        ("2020-03", (2020, 3)),
        ("2020-10", (2020, 10)),
        ("2020-12-01", (2020, 12)),
        ("2019-2021", (2019, None)),
        ("2019-20", (2019, None)),
        ("15/03/2020", (2020, 3)),
        ("03/15/2020", (2020, None)),
        ("Present", (2026, 10)),
        ("current", (2026, 10)),
        ("Currently", (2026, 10)),
        ("Till date", (2026, 10)),
        ("n/a", None),
        ("", None),
        (None, None),
        ("06.2021", (2021, 6)),
        ("January 2020", (2020, 1)),
        ("Marketing 2020", (2020, None)),
        ("Decision 2019", (2019, None)),
    ],
)
def test_read_date(text: str | None, expected: tuple[int, int | None] | None) -> None:
    assert read_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("Jan 2026", "Present", "10 mos"),
        ("Oct 2026", "Oct 2026", "1 mo"),
        ("Jan 2020", "Nov 2020", "11 mos"),
        ("Jan 2020", "Dec 2020", "1 yr"),
        ("Mar 2015", "Present", "11 yrs"),
        ("2021", "2023", "2 yrs"),
        ("2022", "2023", "1 yr"),
        ("2021", "Jan 2023", "2 yrs"),
        ("2023", "2023", None),
        ("2023", "2021", None),
        ("May 2022", "Jan 2022", None),
        ("n/a", "2020", None),
        (None, "2020", None),
        ("2020", None, None),
    ],
)
def test_format_duration(start: str | None, end: str | None, expected: str | None) -> None:
    assert format_duration(start, end, TODAY) == expected


@pytest.mark.parametrize(
    ("spans", "expected"),
    [
        pytest.param([("Dec 2024", "Present")], "1 year", id="23-months-floored"),
        pytest.param([("2010", "2020"), ("2015", "2018")], "10 years", id="ends-at-latest-end"),
        pytest.param([("Jan 2003", "Dec 2010"), ("2012", "Present")], "23 years", id="gap-counts"),
        pytest.param([("Mar 2026", "Present")], None, id="8-months-is-0"),
        pytest.param([("2020", None)], None, id="no-readable-end"),
        pytest.param([("n/a", "2020")], None, id="no-readable-start"),
        pytest.param([], None, id="no-jobs"),
    ],
)
def test_total_years(spans: list[tuple[str | None, str | None]], expected: str | None) -> None:
    assert total_years(spans, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "• Led team\n- Built API\n\n * Wrote docs\n· Shipped",
            ["Led team", "Built API", "Wrote docs", "Shipped"],
        ),
        (
            "– Planned\n▪ Built\n● Tested\n◦ Fixed\n➢ Shipped\n Reported",
            ["Planned", "Built", "Tested", "Fixed", "Shipped", "Reported"],
        ),
        ("Built and ran the API.", ["Built and ran the API."]),
        (None, []),
        (
            "-30% latency\n-5 incidents\n*Important* thing\n- Led the team\n•\nOne\vtwo\x1cthree",
            [
                "-30% latency",
                "-5 incidents",
                "*Important* thing",
                "Led the team",
                "One\vtwo\x1cthree",
            ],
        ),
    ],
    ids=["common-markers", "more-markers", "one-paragraph", "none", "marker-needs-whitespace"],
)
def test_split_bullets(text: str | None, expected: list[str]) -> None:
    assert split_bullets(text) == expected


def test_html_escapes_ai_text() -> None:
    data = MINIMAL_DATA.model_copy(update={"name": "<script>alert(1)</script>"})

    html = build_resume_html(data, TODAY)

    assert "&lt;script&gt;" in html
    assert "<script" not in html


def test_only_http_links_become_hrefs() -> None:
    links = ["https://example.com/jane", "javascript:alert(1)", "www.janedoe.dev"]
    data = MINIMAL_DATA.model_copy(update={"links": links})

    html = build_resume_html(data, TODAY)

    assert re.findall(r'href="([^"]*)"', html) == ["https://example.com/jane"]
    assert "javascript:alert(1)" in html
    assert "www.janedoe.dev" in html
    for label in ("LINK 1", "LINK 2", "LINK 3"):
        assert label in html


@pytest.mark.parametrize(
    ("link", "is_clickable"),
    [
        ("https://example.com/a", True),
        ("HTTPS://Example.com/Upper", True),
        ("Http://mixed.example.com", True),
        ("javascript:alert(1)", False),
        ("JaVaScRiPt:alert(1)", False),
        ("data:text/html,<b>hi</b>", False),
        ("file:///C:/Windows/win.ini", False),
        ("mailto:jane@example.com", False),
        ("ftp://files.example.com", False),
    ],
)
def test_only_http_and_https_schemes_become_hrefs_in_links_and_projects(
    link: str, is_clickable: bool
) -> None:
    project = Project(name="Parser", description=None, link=link, technologies=[])
    data = MINIMAL_DATA.model_copy(update={"links": [link], "projects": [project]})

    html = build_resume_html(data, TODAY)

    assert re.findall(r'href="([^"]*)"', html) == ([link, link] if is_clickable else [])


def collect_markup(html: str) -> tuple[set[str], set[str]]:
    """Return the tag names and attribute names the HTML really contains."""
    tags: set[str] = set()
    attributes: set[str] = set()

    class Collector(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tags.add(tag)
            attributes.update(name for name, _ in attrs)

    Collector().feed(html)
    return tags, attributes


def test_ai_text_in_every_field_never_becomes_markup() -> None:
    """Fill every text field with markup and check no new tag or attribute (onerror...) appears."""
    payload = MARKUP_PAYLOAD
    data = ResumeData(
        name=payload,
        email=payload,
        phone=payload,
        location=payload,
        links=[payload, f"https://example.com/{payload}"],
        skills=[payload],
        experience=[
            Experience(
                company=payload,
                title=payload,
                start_date=payload,
                end_date=payload,
                location=payload,
                description=f"{payload}\n• {payload}",
            )
        ],
        education=[
            Education(
                institution=payload,
                degree=payload,
                field_of_study=payload,
                start_date=payload,
                end_date=payload,
            )
        ],
        projects=[Project(name=payload, description=payload, link=payload, technologies=[payload])],
        certificates=[Certificate(name=payload, issuer=payload, date=payload)],
    )
    normal_tags, normal_attributes = collect_markup(build_resume_html(FAKE_RESUME_DATA, TODAY))

    tags, attributes = collect_markup(build_resume_html(data, TODAY))

    assert tags <= normal_tags
    assert attributes <= normal_attributes


def test_blank_values_are_hidden_and_links_are_stripped() -> None:
    data = MINIMAL_DATA.model_copy(
        update={
            "phone": " ",
            "links": ["", " https://example.com/jane ", "  "],
            "skills": ["", " ", "Python"],
            "experience": [
                Experience(
                    company=None,
                    title=" ",
                    start_date=None,
                    end_date=None,
                    location="",
                    description=None,
                )
            ],
            "certificates": [Certificate(name=None, issuer=None, date=None)],
        }
    )

    html = build_resume_html(data, TODAY)

    assert re.findall(r'href="([^"]*)"', html) == ["https://example.com/jane"]
    assert ">https://example.com/jane<" in html
    assert "LINK 1" in html
    assert "LINK 2" not in html
    assert re.findall(r'<li class="pill">([^<]*)</li>', html) == ["Python"]
    for hidden in ("Phone number", "Employment History", "Licenses & Certifications", "tile tile"):
        assert hidden not in html


def test_empty_sections_are_hidden() -> None:
    html = build_resume_html(MINIMAL_DATA, TODAY)

    for heading in [*HEADINGS, "WORK EXPERIENCE IN YEARS"]:
        assert heading not in html
    assert "AREAS OF GIFTING" not in html.upper()


def test_full_data_shows_every_section() -> None:
    html = build_resume_html(FAKE_RESUME_DATA, TODAY)

    for heading in HEADINGS:
        assert heading in html
    assert "WORK EXPERIENCE IN YEARS" in html
    assert "6 years" in html  # Jan 2020 to Present (Oct 2026)
    assert "Springfield, USA" in html
    assert "<li>Builds APIs.</li>" in html
    assert "AREAS OF GIFTING" not in html.upper()


@pytest.mark.parametrize(
    ("start_date", "end_date", "chip"),
    [("2015", "2019", "2019"), ("Sept 2015", None, "Sept 2015")],
    ids=["end-date", "start-date-when-no-end"],
)
def test_education_chip_shows_a_date_as_written(
    start_date: str, end_date: str | None, chip: str
) -> None:
    school = Education(
        institution="State University",
        degree="BSc",
        field_of_study=None,
        start_date=start_date,
        end_date=end_date,
    )
    data = MINIMAL_DATA.model_copy(update={"education": [school]})

    html = build_resume_html(data, TODAY)

    assert f'<span class="chip">{chip}</span>' in html


def test_html_loads_nothing_from_outside() -> None:
    """The only http(s) URLs are the AI's own links, and every asset is a data: URI."""
    html = build_resume_html(FAKE_RESUME_DATA, TODAY)

    assert set(re.findall(r"https?://[^\s\"'<>)]+", html)) <= set(FAKE_RESUME_DATA.links)
    assert all(src.startswith("data:") for src in re.findall(r'src="([^"]*)"', html))
    assert all(url.startswith("data:") for url in re.findall(r"url\(([^)]*)\)", html))
    assert "url(data:font/woff2;base64," in html


def test_playwright_failure_becomes_pdf_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_sync_playwright() -> None:
        raise playwright.sync_api.Error("boom")

    monkeypatch.setattr(service, "sync_playwright", failing_sync_playwright)

    with pytest.raises(PdfRenderError) as error:
        render_pdf("<p>Jane Doe</p>")
    assert str(error.value) == PDF_FAILED_MESSAGE


def test_real_render_has_a_header_and_footer_on_every_page() -> None:
    """The one real render (user-approved): needs Chromium from `playwright install`.

    It fails rather than skips, because the endpoint can't work without Chromium either.
    """
    jobs = [
        Experience(
            company=f"Company {number}",
            title=f"Engineer {number}",
            start_date="Jan 2010",
            end_date="Dec 2011",
            location="Remote",
            description="\n".join(f"• Synthetic achievement {line}" for line in range(5)),
        )
        for number in range(12)
    ]
    # A 100-character unbroken title must wrap (an overflow makes Chrome shrink every page), and
    # a first job longer than a page must start on page 1 (its title with its first bullet).
    jobs[0] = jobs[0].model_copy(
        update={
            "title": ("abcdefghijklmnopqrstuvwxyz" * 4)[:100],
            "description": "\n".join(f"• Long first job line {line}" for line in range(40)),
        }
    )
    data = FAKE_RESUME_DATA.model_copy(update={"experience": jobs})

    pdf_bytes = render_pdf(build_resume_html(data, TODAY))

    assert pdf_bytes.startswith(b"%PDF-")
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        assert len(document) >= 2
        width, height = document[0].get_size()
        assert width == pytest.approx(595.3, abs=1)
        assert height == pytest.approx(841.9, abs=1)
        page_one_text = document[0].get_textpage().get_text_range()
        assert "Jane Doe" in page_one_text
        assert "Long first job line 0" in page_one_text
        # The right-aligned date chips end near the content edge (595.92 pt - 13.2 mm): not
        # beyond it (overflow), and not far inside it (a shrunken page).
        right_edges = [
            text.get_bounds()[2]
            for text in document[0].get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT])
        ]
        assert 540 < max(right_edges) <= 559
        fonts = set()
        for index in range(len(document)):
            page = document[index]
            images = list(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]))
            assert len(images) >= 2, f"page {index + 1} lacks the header or footer"
            for text in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT]):
                name = ctypes.create_string_buffer(128)
                font = pdfium_c.FPDFTextObj_GetFont(text.raw)
                pdfium_c.FPDFFont_GetBaseFontName(font, name, len(name))
                fonts.add(name.value.decode())
        # Proves the web fonts loaded before printing (page.pdf doesn't wait for them).
        assert fonts and all("Inter" in font for font in fonts)
    finally:
        document.close()

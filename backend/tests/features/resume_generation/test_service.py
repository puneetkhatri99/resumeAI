"""Tests for the resume_generation service: date reading, durations, total years, bullets, the
HTML page, and the PDF renderer. Only test_real_render_has_a_header_and_footer_on_every_page
starts Chromium (user-approved); no test calls OpenAI.
"""

import ctypes
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from html.parser import HTMLParser

import playwright.sync_api
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

from app.features.resume_generation import service
from app.features.resume_generation.service import (
    MAX_CONCURRENT_RENDERS,
    PDF_FAILED_MESSAGE,
    PdfRenderError,
    build_resume_html,
    format_duration,
    format_phone,
    group_links,
    is_current,
    order_experience,
    read_date,
    render_pdf,
    split_bullets,
    total_years,
)
from app.features.resume_structuring.schemas import (
    Certificate,
    Education,
    Experience,
    InferredSkill,
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
    assert "Other Links" in html
    assert "LINK 1" not in html


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
    assert "Other Links" in html
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
    [
        ("2015", "2019", "2019"),
        ("2024", "Expected 2026", "Expected 2026"),
        ("2023", "Present", "In progress"),
        ("Sept 2015", None, None),
    ],
    ids=["graduation-date", "expected", "ongoing", "start-date-alone-shows-no-chip"],
)
def test_education_chip_shows_only_the_graduation_date(
    start_date: str, end_date: str | None, chip: str | None
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

    chips = re.findall(r'<span class="chip">([^<]*)</span>', html)
    assert chips == ([chip] if chip else [])


def test_html_loads_nothing_from_outside() -> None:
    """The only http(s) URLs are the AI's own links, and every asset is a data: URI."""
    html = build_resume_html(FAKE_RESUME_DATA, TODAY)

    assert set(re.findall(r"https?://[^\s\"'<>)]+", html)) <= set(FAKE_RESUME_DATA.links)
    assert all(src.startswith("data:") for src in re.findall(r'src="([^"]*)"', html))
    assert all(url.startswith("data:") for url in re.findall(r"url\(([^)]*)\)", html))
    assert "url(data:font/woff2;base64," in html


def job(title: str, start: str | None, end: str | None) -> Experience:
    """A synthetic job with only a title and dates."""
    return Experience(
        company=None, title=title, start_date=start, end_date=end, location=None, description=None
    )


@pytest.mark.parametrize(
    ("end_date", "expected"),
    [("Present", True), ("Current", True), ("Till date", True), ("2020", False), (None, False)],
)
def test_is_current(end_date: str | None, expected: bool) -> None:
    assert is_current(job("Pastor", "2019", end_date)) is expected


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        pytest.param(
            [
                job("Old", "2010", "2014"),
                job("Cur A", "2015", "Present"),
                job("Cur B", "2021", "Now"),
            ],
            ["Cur B", "Cur A", "Old"],
            id="current-on-top-newest-start-first",
        ),
        pytest.param(
            [job("A", "2010", "2015"), job("B", "2012", "2018"), job("C", "2013", "2015")],
            ["B", "C", "A"],
            id="past-by-end-then-start",
        ),
        pytest.param(
            [job("Jun", "2010", "Jun 2015"), job("Year", "2009", "2015")],
            ["Year", "Jun"],
            id="year-only-end-counts-as-december",
        ),
        pytest.param(
            [job("No start", None, "Present"), job("Dated", "2020", "Present")],
            ["Dated", "No start"],
            id="current-without-start-after-dated-current",
        ),
        pytest.param(
            [job("X", None, None), job("Past", "2010", "2012"), job("Y", "n/a", "n/a")],
            ["Past", "X", "Y"],
            id="undatable-last-in-cv-order",
        ),
        pytest.param(
            [job("Start only", "2016", None), job("Ended", "2010", "2015")],
            ["Start only", "Ended"],
            id="start-date-when-end-unreadable",
        ),
    ],
)
def test_order_experience(jobs: list[Experience], expected: list[str]) -> None:
    assert [entry.title for entry in order_experience(jobs, TODAY)] == expected


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("5550100100", "(555) 010-0100"),
        ("555-010-0100", "(555) 010-0100"),
        ("(555) 010 0100", "(555) 010-0100"),
        ("+1 555.010.0100", "(555) 010-0100"),
        ("1-555-010-0100", "(555) 010-0100"),
        ("+44 20 7946 0958", "+44 20 7946 0958"),
        ("555-0100 x12", "555-0100 x12"),
        ("555 0100 / 555 0101", "555 0100 / 555 0101"),
        ("12345", "12345"),
        ("2-555-010-0100", "2-555-010-0100"),
        (None, None),
    ],
)
def test_format_phone(phone: str | None, expected: str | None) -> None:
    assert format_phone(phone) == expected


SOCIAL, VIDEOS, OTHER = "Social Media", "Teaching & Worship Videos", "Other Links"


@pytest.mark.parametrize(
    ("link", "label"),
    [
        ("https://www.linkedin.com/in/janedoe", SOCIAL),
        ("https://www.facebook.com/gracechurch", SOCIAL),
        ("https://m.facebook.com/profile.php?id=1", SOCIAL),
        ("https://instagram.com/reel/abc", SOCIAL),
        ("https://www.tiktok.com/@jane/video/1", SOCIAL),
        ("x.com/janedoe", SOCIAL),
        ("https://twitter.com/janedoe", SOCIAL),
        ("https://www.threads.net/@jane", SOCIAL),
        ("https://www.youtube.com/@gracechurch", VIDEOS),
        ("https://youtube.com/playlist?list=1", VIDEOS),
        ("https://youtu.be/abc", VIDEOS),
        ("https://vimeo.com/123", VIDEOS),
        ("https://www.facebook.com/gracechurch/videos/123", VIDEOS),
        ("https://www.facebook.com/reel/123", VIDEOS),
        ("https://www.facebook.com/watch/?v=1", VIDEOS),
        ("https://fb.watch/abc", VIDEOS),
        ("https://open.spotify.com/show/1", OTHER),
        ("https://gracechurch.org/sermons/easter", OTHER),
        ("https://fox.com/news", OTHER),  # not x.com
        ("https://janedoe.dev", OTHER),
        ("GitHub", OTHER),
        ("[::1", OTHER),  # urlsplit raises ValueError
    ],
)
def test_group_links_puts_each_link_in_its_group(link: str, label: str) -> None:
    assert group_links([link]) == [(label, [link])]


def test_group_links_orders_groups_strips_and_drops_duplicates() -> None:
    links = [
        "https://janedoe.dev",
        " https://youtu.be/a ",
        "https://linkedin.com/in/j",
        "https://youtu.be/a",
    ]

    assert group_links(links) == [
        (SOCIAL, ["https://linkedin.com/in/j"]),
        (VIDEOS, ["https://youtu.be/a"]),
        (OTHER, ["https://janedoe.dev"]),
    ]
    assert group_links([]) == []


def test_summary_is_shown_above_the_years() -> None:
    html = build_resume_html(FAKE_RESUME_DATA, TODAY)

    assert '<p class="summary">Software engineer who builds APIs.</p>' in html
    assert html.index('class="summary"') < html.index("WORK EXPERIENCE IN YEARS")


def test_summary_alone_shows_the_section_and_nothing_hides_it() -> None:
    with_summary = build_resume_html(MINIMAL_DATA.model_copy(update={"summary": "Pastor."}), TODAY)
    without = build_resume_html(MINIMAL_DATA, TODAY)

    assert "Professional Summary" in with_summary
    assert "WORK EXPERIENCE IN YEARS" not in with_summary
    assert "Professional Summary" not in without


def test_summary_is_escaped() -> None:
    data = MINIMAL_DATA.model_copy(update={"summary": MARKUP_PAYLOAD})

    html = build_resume_html(data, TODAY)

    assert "&lt;script&gt;" in html
    assert "<script" not in html


@pytest.mark.parametrize(
    ("start", "chips"),
    [("Jan 2020", ["Jan 2020", "Present"]), (None, ["Present"])],
    ids=["with-start", "no-start"],
)
def test_a_current_job_shows_present_whatever_was_written(
    start: str | None, chips: list[str]
) -> None:
    data = MINIMAL_DATA.model_copy(update={"experience": [job("Pastor", start, "Current")]})

    html = build_resume_html(data, TODAY)

    assert re.findall(r'<span class="chip[^"]*">([^<]*)</span>', html) == chips
    assert '<span class="chip chip-current">Present</span>' in html
    assert "Current<" not in html


def test_jobs_are_shown_in_order_and_the_phone_is_formatted() -> None:
    jobs = [job("Old", "2010", "2012"), job("Now", "2018", "Present")]
    data = MINIMAL_DATA.model_copy(update={"experience": jobs, "phone": "+1 555.010.0100"})

    html = build_resume_html(data, TODAY)

    assert html.index(">Now<") < html.index(">Old<")
    assert ">(555) 010-0100<" in html


def test_links_show_under_their_group_labels() -> None:
    links = ["https://linkedin.com/in/j", "https://youtu.be/a", "https://janedoe.dev"]

    html = build_resume_html(MINIMAL_DATA.model_copy(update={"links": links}), TODAY)

    labels = re.findall(r'<p class="label">([^<]*)</p>', html)
    assert labels[-3:] == ["Social Media", "Teaching &amp; Worship Videos", "Other Links"]
    assert "LINK 1" not in html


def test_inferred_skills_follow_the_listed_skills_unmarked() -> None:
    inferred = [InferredSkill(name="Preaching", evidence="preached weekly sermons")]
    data = MINIMAL_DATA.model_copy(update={"skills": ["Python"], "inferred_skills": inferred})

    html = build_resume_html(data, TODAY)

    assert re.findall(r'<li class="pill">([^<]*)</li>', html) == ["Python", "Preaching"]
    assert "preached weekly sermons" not in html


def test_an_old_row_without_the_new_fields_renders() -> None:
    stored = FAKE_RESUME_DATA.model_dump(exclude={"summary", "inferred_skills"})

    html = build_resume_html(ResumeData.model_validate(stored), TODAY)

    assert "Professional Summary" in html  # from the years alone
    assert 'class="summary"' not in html
    # The project's technology, then the listed skills; no inferred ones.
    assert re.findall(r'<li class="pill">([^<]*)</li>', html) == ["FastAPI", "Python", "SQL"]


def test_playwright_failure_becomes_pdf_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_sync_playwright() -> None:
        raise playwright.sync_api.Error("boom")

    monkeypatch.setattr(service, "sync_playwright", failing_sync_playwright)

    with pytest.raises(PdfRenderError) as error:
        render_pdf("<p>Jane Doe</p>")
    assert str(error.value) == PDF_FAILED_MESSAGE


def test_at_most_two_renders_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each fake "browser" stays open briefly, then fails; 6 threads must never overlap past 2."""
    lock = threading.Lock()
    running, peak = 0, 0

    @contextmanager
    def slow_sync_playwright() -> Iterator[None]:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            time.sleep(0.05)
            raise playwright.sync_api.Error("boom")
        finally:
            with lock:
                running -= 1
        yield  # never reached; makes this a generator

    monkeypatch.setattr(service, "sync_playwright", slow_sync_playwright)

    def render_once() -> None:
        with pytest.raises(PdfRenderError):
            render_pdf("<p>Jane Doe</p>")

    threads = [threading.Thread(target=render_once) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == MAX_CONCURRENT_RENDERS == 2


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
    # Listed last but current, so order_experience puts it first.
    jobs[-1] = jobs[-1].model_copy(
        update={
            "end_date": "Current",
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
        assert "Present" in page_one_text  # the current role, ordered first
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


# --- edge cases (QA pass) ---


@pytest.mark.parametrize(
    ("end_date", "expected"),
    [
        ("present ", True),
        ("PRESENT", True),
        ("Current role", True),
        ("2020 –", False),  # an open range is the AI's job to turn into "Present"
        ("expected 2027", False),
        ("", False),
        ("Nowhere", False),
    ],
)
def test_is_current_with_odd_end_dates(end_date: str, expected: bool) -> None:
    assert is_current(job("Pastor", "2019", end_date)) is expected


@pytest.mark.parametrize("text", ["present ", "Current role"])
def test_read_date_reads_ongoing_words_with_extra_text_as_today(text: str) -> None:
    assert read_date(text, TODAY) == (2026, 10)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2020 –", (2020, None)), ("expected 2027", (2027, None))],
)
def test_read_date_reads_the_year_of_an_open_range_or_expected_date(
    text: str, expected: tuple[int, int | None]
) -> None:
    assert read_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        pytest.param(
            [job("A", "2020", "Present"), job("B", "2020", "Present"), job("C", "2020", "Current")],
            ["A", "B", "C"],
            id="identical-current-jobs-keep-cv-order",
        ),
        pytest.param(
            [job("Year", "2020", "Present"), job("Month", "Jan 2020", "Present")],
            ["Month", "Year"],
            id="current-month-start-before-year-only-start",
        ),
        pytest.param(
            [job("A", "2015", "2015"), job("B", "2015", "2015")],
            ["A", "B"],
            id="identical-past-jobs-keep-cv-order",
        ),
        pytest.param(
            [job("Past", "2010", "2012"), job("Future", "2031", "Present")],
            ["Future", "Past"],
            id="future-start-that-is-current-is-still-on-top",
        ),
        pytest.param(
            [job("Open", "2018", None), job("Ended", "2010", "2012"), job("None", None, None)],
            ["Open", "Ended", "None"],
            id="no-end-uses-the-start-and-undated-is-last",
        ),
        pytest.param([], [], id="empty"),
    ],
)
def test_order_experience_ties_and_gaps(jobs: list[Experience], expected: list[str]) -> None:
    assert [entry.title for entry in order_experience(jobs, TODAY)] == expected


def test_order_experience_returns_a_new_list_and_leaves_the_input_alone() -> None:
    jobs = [job("Old", "2010", "2012"), job("New", "2020", "Present")]

    ordered = order_experience(jobs, TODAY)

    assert [entry.title for entry in jobs] == ["Old", "New"]
    assert ordered is not jobs


@pytest.mark.parametrize(
    "phone",
    [
        "+44 7911 123456",
        "00 1 555 010 0100",
        "5550100100 5",
        "555-010-0100 ext 5",
        "(555) 010-0100 / (555) 010-0101",
        "555 010 0100 555 010 0101",
        "25550100100",
        "",
    ],
)
def test_format_phone_leaves_other_numbers_as_written(phone: str) -> None:
    assert format_phone(phone) == phone


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+1 (555) 010-0100 ", "(555) 010-0100"),
        ("\n555.010.0100", "(555) 010-0100"),
        ("11234567890", "(123) 456-7890"),
    ],
)
def test_format_phone_formats_stray_whitespace_and_a_leading_1(phone: str, expected: str) -> None:
    assert format_phone(phone) == expected


def test_format_phone_never_raises_on_non_ascii_digits() -> None:
    assert isinstance(format_phone("٥٥٥٠١٠٠١٠٠"), str)


def test_format_phone_leaves_a_ten_digit_number_from_another_country_as_written() -> None:
    """The docstring says another country's number stays as written; +45 numbers have 8 digits
    after the country code, so 10 digits in total."""
    assert format_phone("+45 12 34 56 78") == "+45 12 34 56 78"


@pytest.mark.parametrize(
    ("link", "label"),
    [
        ("HTTPS://WWW.LINKEDIN.COM/in/x", SOCIAL),
        ("LinkedIn.com/in/x", SOCIAL),
        ("m.facebook.com/watch", VIDEOS),
        ("https://mobile.facebook.com/reel/1", VIDEOS),
        ("youtu.be/x", VIDEOS),
        ("fb.watch/x", VIDEOS),
        ("https://facebook.com/watch", VIDEOS),
        ("https://facebook.com/Watch/", VIDEOS),
        ("https://facebook.com/watchdogs", SOCIAL),
        ("https://www.facebook.com/videos/123", VIDEOS),
        ("https://www.facebook.com/video.php?v=123", VIDEOS),
        ("https://www.facebook.com/share/v/abc/", VIDEOS),
        ("https://www.facebook.com/share/r/abc/", VIDEOS),
        ("https://www.facebook.com/share/p/abc/", SOCIAL),  # a shared post
        ("https://www.facebook.com/videosbyjane", SOCIAL),
        ("https://YOUTUBE.COM:443/x", VIDEOS),
        ("https://music.youtube.com/x", VIDEOS),
        ("javascript:alert(1)", OTHER),
        ("data:text/html,<script>", OTHER),
        ("http://", OTHER),
        ("https://notyoutube.com", OTHER),
        ("https://youtube.com.evil.com", OTHER),
        ("https://evil.com/?u=youtube.com", OTHER),
        ("https://youtube.com@evil.com", OTHER),
        ("https://x.com.evil.com", OTHER),
        ("https://box.com", OTHER),
        ("https://max.com", OTHER),
        ("https://faceb00k.com", OTHER),
    ],
)
def test_group_links_edge_cases_and_lookalike_hosts(link: str, label: str) -> None:
    assert group_links([link]) == [(label, [link])]


def test_group_links_does_not_raise_on_any_odd_value() -> None:
    odd = ["", "   ", "[::1", "https://[::1", "//youtube.com/x", "\x00", "https://" + "a" * 5000]

    assert group_links(odd)  # every one lands somewhere


@pytest.mark.parametrize("link", ["javascript:alert(1)", "javascript://youtube.com/%0aalert(1)"])
def test_a_javascript_link_is_shown_as_text_never_as_an_href(link: str) -> None:
    data = MINIMAL_DATA.model_copy(update={"links": [link, "https://youtu.be/a"]})

    html = build_resume_html(data, TODAY)

    assert re.findall(r'href="([^"]*)"', html) == ["https://youtu.be/a"]


@pytest.mark.parametrize(
    "summary",
    [
        "</p><script>alert(1)</script>",
        '"><img src=x onerror=alert(1)>',
        "{{ 7*7 }} {% if 1 %}x{% endif %}",
        "&lt;b&gt; &amp;",
    ],
)
def test_a_hostile_summary_is_inert_in_the_html(summary: str) -> None:
    data = MINIMAL_DATA.model_copy(update={"summary": summary})
    normal_tags, normal_attributes = collect_markup(build_resume_html(FAKE_RESUME_DATA, TODAY))

    html = build_resume_html(data, TODAY)

    tags, attributes = collect_markup(html)
    assert tags <= normal_tags
    assert attributes <= normal_attributes
    shown = re.search(r'<p class="summary">(.*?)</p>', html, re.DOTALL)
    assert shown is not None
    assert "49" not in shown.group(1)  # "{{ 7*7 }}" is data, never evaluated


def test_a_hostile_inferred_skill_name_and_phone_are_inert() -> None:
    inferred = [InferredSkill(name=MARKUP_PAYLOAD, evidence="x")]
    data = MINIMAL_DATA.model_copy(update={"inferred_skills": inferred, "phone": MARKUP_PAYLOAD})
    normal_tags, normal_attributes = collect_markup(build_resume_html(FAKE_RESUME_DATA, TODAY))

    tags, attributes = collect_markup(build_resume_html(data, TODAY))

    assert tags <= normal_tags
    assert attributes <= normal_attributes


def test_a_row_with_unknown_extra_keys_renders() -> None:
    stored = {**FAKE_RESUME_DATA.model_dump(), "future_field": {"a": 1}}
    stored["experience"][0]["bonus"] = "x"

    html = build_resume_html(ResumeData.model_validate(stored), TODAY)

    assert "future_field" not in html
    assert "bonus" not in html

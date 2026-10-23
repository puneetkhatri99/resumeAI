"""Tests for the resume_intake service: text extraction and OCR, structure_resume's OCR retry
(no database needed; OpenAI is the autouse fake), and get_resume_intake (needs Postgres;
db_session rolls the row back).
"""

import base64
import codecs
import io
import struct
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import olefile
import pytest
from sqlalchemy.orm import Session

from app.features.ai_usage.models import AiCall
from app.features.resume_intake import service
from app.features.resume_intake.schemas import InterviewAnswers
from app.features.resume_intake.service import (
    EMPTY_MESSAGE,
    LINKS_HEADING,
    OLD_WORD_MESSAGE,
    PASSWORD_MESSAGE,
    PDFIUM_LOCK,
    PIECE_COMPRESSED,
    TOO_MANY_PAGES_MESSAGE,
    UNREADABLE_MESSAGE,
    ResumeExtractionError,
    UnsupportedFileTypeError,
    clean_doc_text,
    extract_text,
    get_resume_intake,
    read_doc_streams,
    save_resume_intake,
    structure_resume,
)
from app.features.resume_structuring.service import MissingContactError
from tests.conftest import FAKE_OCR_TEXT, FAKE_RESUME_DATA, FakeOpenAI

REAL_DOC = Path(r"C:\Windows\System32\MSDRM\MsoIrmProtector.doc")
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
APP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
DOC_TEXT_START = 0x200
OLE_FREE, OLE_END, OLE_FAT_SECTOR, OLE_NO_STREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


def build_zip(parts: dict[str, str]) -> bytes:
    """Return a zip archive holding `parts` (name -> content)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def build_docx(body: str, parts: dict[str, str] | None = None) -> bytes:
    """Return a .docx whose document body is `body` (WordprocessingML), plus any extra parts."""
    document = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}"><w:body>{body}</w:body></w:document>'
    )
    return build_zip({"word/document.xml": document, **(parts or {})})


def paragraph(text: str) -> str:
    """One WordprocessingML paragraph holding `text`."""
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def build_ole(stream_name: str) -> bytes:
    """Return a minimal compound (OLE) file holding one empty stream called `stream_name`."""
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII",
        olefile.MAGIC,
        b"\0" * 16,
        0x3E,  # minor version
        3,  # major version: 512-byte sectors
        0xFFFE,  # byte order
        9,  # sector shift
        6,  # mini sector shift
        b"\0" * 6,
        0,  # directory sectors (always 0 in version 3)
        1,  # FAT sectors
        1,  # first directory sector
        0,  # transaction signature
        4096,  # mini stream cutoff
        OLE_END,  # first mini FAT sector
        0,  # mini FAT sectors
        OLE_END,  # first DIFAT sector
        0,  # DIFAT sectors
    ) + struct.pack("<109I", 0, *[OLE_FREE] * 108)
    fat = struct.pack("<128I", OLE_FAT_SECTOR, OLE_END, *[OLE_FREE] * 126)

    def directory_entry(name: str, kind: int, child: int) -> bytes:
        encoded = (name + "\0").encode("utf-16-le")
        return struct.pack(
            "<64sHBBIII16sIQQIQ",
            encoded,
            len(encoded),
            kind,
            1,  # black
            OLE_NO_STREAM,
            OLE_NO_STREAM,
            child,
            b"\0" * 16,
            0,
            0,
            0,
            OLE_END,
            0,
        )

    directory = (
        directory_entry("Root Entry", 5, 1)
        + directory_entry(stream_name, 2, OLE_NO_STREAM)
        + b"\0" * 256
    )
    return header + fat + directory


def build_doc_streams(
    pieces: list[tuple[int, int, bool]], text_area: bytes, prc: bytes = b""
) -> tuple[bytes, bytes]:
    """Return (WordDocument, table) streams whose piece table describes `pieces`.

    Each piece is (byte offset into `text_area`, character count, compressed). `prc` goes in
    front of the piece table inside the CLX.
    """
    cps = [0]
    pcds = b""
    for offset, char_count, compressed in pieces:
        cps.append(cps[-1] + char_count)
        fc = DOC_TEXT_START + offset
        raw_fc = (fc * 2) | PIECE_COMPRESSED if compressed else fc
        pcds += struct.pack("<HIH", 0, raw_fc, 0)
    plc_pcd = struct.pack(f"<{len(cps)}I", *cps) + pcds
    clx = prc + b"\x02" + struct.pack("<I", len(plc_pcd)) + plc_pcd
    fib = bytearray(DOC_TEXT_START)
    struct.pack_into("<II", fib, 0x1A2, 0, len(clx))
    return bytes(fib) + text_area, clx


# --- TXT ---


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("Jane Doe — Engineer".encode(), "Jane Doe — Engineer"),
        (codecs.BOM_UTF8 + "Café".encode(), "Café"),
        ("Café".encode("cp1252"), "Café"),
        ("Café\r\nLine 2".encode("utf-16"), "Café\nLine 2"),
        (b"Caf\x81", "Caf\ufffd"),
        (b"  a\r\nb\rc\x01d\x1be\tf  ", "a\nb\ncde\tf"),
    ],
    ids=["utf8", "utf8-bom", "cp1252", "utf16-bom", "undefined-cp1252-byte", "control-chars"],
)
def test_txt_is_decoded_and_cleaned(data: bytes, expected: str) -> None:
    assert extract_text(data, []) == ("txt", expected, False)


# --- DOCX ---


def test_docx_reads_header_body_table_and_footer_in_order() -> None:
    table = f"<w:tbl><w:tr><w:tc>{paragraph('Cell')}</w:tc></w:tr></w:tbl>"
    data = build_docx(
        paragraph("Body") + table,
        {
            "word/header1.xml": f'<w:hdr xmlns:w="{W_NS}">{paragraph("Header")}</w:hdr>',
            "word/footer1.xml": f'<w:ftr xmlns:w="{W_NS}">{paragraph("Footer")}</w:ftr>',
        },
    )

    assert extract_text(data, []) == ("docx", "Header\nBody\nCell\nFooter", False)


def test_docx_text_box_in_alternate_content_appears_once() -> None:
    box = f"<w:txbxContent>{paragraph('Box')}</w:txbxContent>"
    body = (
        "<w:p><w:r><mc:AlternateContent>"
        f"<mc:Choice Requires='wps'><w:drawing>{box}</w:drawing></mc:Choice>"
        f"<mc:Fallback><w:pict>{box}</w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )

    assert extract_text(build_docx(body), []) == ("docx", "Box", False)


def test_docx_tab_stops_add_no_stray_tab() -> None:
    body = (
        "<w:p><w:pPr><w:tabs><w:tab w:val='left' w:pos='720'/></w:tabs></w:pPr>"
        "<w:r><w:t>A</w:t></w:r><w:r><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t>"
        "<w:noBreakHyphen/><w:t>D</w:t></w:r></w:p>"
    )

    assert extract_text(build_docx(body), []) == ("docx", "A\tB\nC-D", False)


@pytest.mark.parametrize(("pages", "allowed"), [(4, True), (5, False)])
def test_docx_page_limit_uses_saved_page_count(pages: int, allowed: bool) -> None:
    app_xml = f'<Properties xmlns="{APP_NS}"><Pages>{pages}</Pages></Properties>'
    data = build_docx(paragraph("CV"), {"docProps/app.xml": app_xml})

    if allowed:
        assert extract_text(data, []) == ("docx", "CV", False)
    else:
        with pytest.raises(ResumeExtractionError, match=TOO_MANY_PAGES_MESSAGE):
            extract_text(data, [])


def test_docx_over_size_cap_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "MAX_DOCX_XML_BYTES", 100)

    with pytest.raises(ResumeExtractionError, match="too large"):
        extract_text(build_docx(paragraph("x" * 200)), [])


def entity_bomb_document() -> str:
    """A document.xml whose last entity expands to about 10^12 characters (billion laughs)."""
    entities = '<!ENTITY e0 "' + "&a;" * 10 + '">'
    for level in range(1, 9):
        entities += f'<!ENTITY e{level} "' + f"&e{level - 1};" * 10 + '">'
    return (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "{"x" * 1000}">{entities}]>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{paragraph("&e8;")}</w:body></w:document>'
    )


@pytest.mark.parametrize(
    "data",
    [
        build_zip({"word/document.xml": entity_bomb_document()}),
        build_docx(
            paragraph("CV"),
            {"docProps/app.xml": f"<Properties xmlns='{APP_NS}'><Pages>x</Pages></Properties>"},
        ),
        build_zip({"word/document.xml": f"<w:document xmlns:w='{W_NS}'>" + "<w:p>" * 5000}),
        build_zip(
            {
                "word/document.xml": f"<w:document xmlns:w='{W_NS}'>"
                + "<w:p>" * 5000
                + "</w:p>" * 5000
                + "</w:document>"
            }
        ),
    ],
    ids=["entity-bomb", "non-numeric-page-count", "unclosed-tags", "5000-levels-deep"],
)
def test_malformed_docx_xml_is_unreadable_not_a_crash(data: bytes) -> None:
    with pytest.raises(ResumeExtractionError) as error:
        extract_text(data, [])
    assert str(error.value) == UNREADABLE_MESSAGE


# --- PDF ---


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        (["Jane Doe, Engineer"], "Jane Doe, Engineer"),
        (["Page one", "Page two"], "Page one\n\nPage two"),
    ],
)
def test_pdf_text_layer_is_read_without_ocr(
    make_pdf: Callable[..., bytes], fake_openai: FakeOpenAI, pages: list[str], expected: str
) -> None:
    assert extract_text(make_pdf(pages), []) == ("pdf", expected, False)
    assert fake_openai.ocr_calls == []


def test_pdf_link_addresses_are_appended_once_and_only_web_links(
    make_pdf: Callable[..., bytes],
) -> None:
    pdf = make_pdf(
        ["Jane Doe GitHub LinkedIn www.janedoe.dev"],
        link_urls=(
            "https://github.com/janedoe",
            "https://www.linkedin.com/in/janedoe",
            "https://github.com/janedoe",
            "www.janedoe.dev",
            "mailto:jane.doe@example.com",
        ),
    )

    assert extract_text(pdf, []) == (
        "pdf",
        "Jane Doe GitHub LinkedIn www.janedoe.dev\n\nLinks in the PDF:\n"
        "https://github.com/janedoe\nhttps://www.linkedin.com/in/janedoe",
        False,
    )


def test_pdf_with_four_pages_is_accepted(make_pdf: Callable[..., bytes]) -> None:
    assert extract_text(make_pdf(["1", "2", "3", "4"]), []) == ("pdf", "1\n\n2\n\n3\n\n4", False)


def test_pdf_without_a_text_layer_is_read_by_ocr(
    make_pdf: Callable[..., bytes], fake_openai: FakeOpenAI
) -> None:
    scan = make_pdf(["", ""])
    fake_openai.ocr_text = "  Jane Doe\r\njane.doe@example.com\x00\n"
    ai_calls: list[AiCall] = []

    assert extract_text(scan, ai_calls) == ("pdf", "Jane Doe\njane.doe@example.com", True)
    assert len(fake_openai.ocr_calls) == 1
    assert [call.call_type for call in ai_calls] == ["ocr"]
    sent_file = fake_openai.ocr_calls[0]["input"][0]["content"][0]["file_data"]
    assert sent_file == "data:application/pdf;base64," + base64.b64encode(scan).decode()


def test_pdf_whose_ocr_finds_no_text_is_empty(
    make_pdf: Callable[..., bytes], fake_openai: FakeOpenAI
) -> None:
    fake_openai.ocr_text = " \n\t"

    with pytest.raises(ResumeExtractionError) as error:
        extract_text(make_pdf([""]), [])
    assert str(error.value) == EMPTY_MESSAGE


def test_the_pdfium_lock_is_not_held_while_openai_reads_the_scan(
    make_pdf: Callable[..., bytes], fake_openai: FakeOpenAI
) -> None:
    """OCR takes 10-30 s; holding the lock would block every other PDF upload meanwhile."""
    lock_held_during_ocr = []
    ocr = fake_openai.create

    def spy(**kwargs: object) -> SimpleNamespace:
        lock_held_during_ocr.append(PDFIUM_LOCK.locked())
        return ocr(**kwargs)

    fake_openai.responses.create = spy

    extract_text(make_pdf([""]), [])

    assert lock_held_during_ocr == [False]


@pytest.mark.parametrize(
    ("pages", "encrypted", "message"),
    [
        (["1", "2", "3", "4", "5"], False, TOO_MANY_PAGES_MESSAGE),
        ([""] * 5, False, TOO_MANY_PAGES_MESSAGE),
        (["Secret"], True, PASSWORD_MESSAGE),
    ],
    ids=["five-pages", "five-page-scan", "password"],
)
def test_pdf_rejections_come_before_any_ocr(
    make_pdf: Callable[..., bytes],
    fake_openai: FakeOpenAI,
    pages: list[str],
    encrypted: bool,
    message: str,
) -> None:
    with pytest.raises(ResumeExtractionError) as error:
        extract_text(make_pdf(pages, encrypted=encrypted), [])
    assert str(error.value) == message
    assert fake_openai.ocr_calls == []


# --- structure_resume: the OCR retry when the AI finds no name or email ---

BLANK_EMAIL = FAKE_RESUME_DATA.model_copy(update={"email": ""})
LINKS = "https://github.com/janedoe\nhttps://www.linkedin.com/in/janedoe"


def test_text_with_a_name_and_email_is_structured_once(fake_openai: FakeOpenAI) -> None:
    assert structure_resume(b"%PDF-", "pdf", "Jane Doe", False, []) == (
        "Jane Doe",
        FAKE_RESUME_DATA,
    )
    assert len(fake_openai.calls) == 1
    assert fake_openai.ocr_calls == []


def test_pdf_missing_a_contact_is_ocrd_once_and_keeps_its_links(
    fake_openai: FakeOpenAI,
) -> None:
    fake_openai.results = [BLANK_EMAIL]
    text = "Garbled text layer" + LINKS_HEADING + LINKS
    expected_text = FAKE_OCR_TEXT + LINKS_HEADING + LINKS
    ai_calls: list[AiCall] = []

    result = structure_resume(b"%PDF-", "pdf", text, False, ai_calls)

    assert result == (expected_text, FAKE_RESUME_DATA)
    assert [call["input"] for call in fake_openai.calls] == [text, expected_text]
    assert len(fake_openai.ocr_calls) == 1
    assert [call.call_type for call in ai_calls] == ["structuring", "ocr_retry", "structuring"]


def test_pdf_without_links_is_retried_with_the_ocr_text_alone(fake_openai: FakeOpenAI) -> None:
    fake_openai.results = [BLANK_EMAIL]

    assert structure_resume(b"%PDF-", "pdf", "Garbled", False, [])[0] == FAKE_OCR_TEXT


@pytest.mark.parametrize(
    ("file_type", "read_by_ocr"),
    [("txt", False), ("docx", False), ("doc", False), ("pdf", True)],
    ids=["txt", "docx", "doc", "pdf-already-ocrd"],
)
def test_no_ocr_retry_unless_a_pdf_was_read_from_its_text_layer(
    fake_openai: FakeOpenAI, file_type: str, read_by_ocr: bool
) -> None:
    fake_openai.result = BLANK_EMAIL

    with pytest.raises(MissingContactError):
        structure_resume(b"data", file_type, "Jane Doe", read_by_ocr, [])
    assert len(fake_openai.calls) == 1
    assert fake_openai.ocr_calls == []


def test_a_second_miss_after_ocr_is_a_missing_contact_error(fake_openai: FakeOpenAI) -> None:
    fake_openai.result = BLANK_EMAIL

    with pytest.raises(MissingContactError):
        structure_resume(b"%PDF-", "pdf", "Garbled", False, [])
    assert len(fake_openai.calls) == 2
    assert len(fake_openai.ocr_calls) == 1


def test_empty_ocr_on_retry_reraises_the_original_error(fake_openai: FakeOpenAI) -> None:
    fake_openai.result = BLANK_EMAIL
    fake_openai.ocr_text = "\n \x00"

    with pytest.raises(MissingContactError):
        structure_resume(b"%PDF-", "pdf", "Garbled", False, [])
    assert len(fake_openai.calls) == 1
    assert len(fake_openai.ocr_calls) == 1


# --- DOC ---


def test_doc_streams_read_compressed_and_utf16_pieces() -> None:
    text_area = b"Hello " + "Wörld".encode("utf-16-le")
    word_stream, table_stream = build_doc_streams([(0, 6, True), (6, 5, False)], text_area)

    assert read_doc_streams(word_stream, table_stream) == "Hello Wörld"


def test_doc_streams_skip_prc_blocks() -> None:
    prc = b"\x01" + struct.pack("<H", 3) + b"abc" + b"\x01" + struct.pack("<H", 0)
    word_stream, table_stream = build_doc_streams([(0, 5, True)], b"Hello", prc)

    assert read_doc_streams(word_stream, table_stream) == "Hello"


def test_doc_streams_large_cb_grpprl_ends_the_loop() -> None:
    prc = b"\x01" + struct.pack("<H", 0xFFFF)
    word_stream, table_stream = build_doc_streams([(0, 5, True)], b"Hello", prc)

    with pytest.raises(ResumeExtractionError, match=UNREADABLE_MESSAGE):
        read_doc_streams(word_stream, table_stream)


def test_doc_streams_overlapping_pieces_are_rejected() -> None:
    word_stream, table_stream = build_doc_streams([(0, 600, True), (0, 600, True)], b"x" * 600)

    with pytest.raises(ResumeExtractionError, match=UNREADABLE_MESSAGE):
        read_doc_streams(word_stream, table_stream)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Name: \x13 HYPERLINK \x13 REF x \x14inner\x15 \x14Jane\x15!", "Name: Jane!"),
        ("\x13 IF \x14before \x13 PAGE \x141\x15 after\x15", "before 1 after"),
        ("\x13 PAGE \x15no result", "no result"),
        ("a\x15b\x14c", "abc"),
        ("a\rb\x0bc\x0cd\x07e\x1ef", "a\nb\nc\nd\ne-f"),
    ],
    ids=["nested-in-instruction", "nested-in-result", "no-result", "unmatched", "special-chars"],
)
def test_clean_doc_text(raw: str, expected: str) -> None:
    assert clean_doc_text(raw) == expected


@pytest.mark.skipif(not REAL_DOC.exists(), reason="Word 97 sample file not on this machine")
def test_real_word_97_doc_is_read() -> None:
    file_type, text, read_by_ocr = extract_text(REAL_DOC.read_bytes(), [])

    assert file_type == "doc"
    assert not read_by_ocr
    assert "This document has been IRM protected by policy." in text


@pytest.mark.skipif(not REAL_DOC.exists(), reason="Word 97 sample file not on this machine")
@pytest.mark.parametrize(("pages", "allowed"), [(4, True), (5, False)])
def test_doc_page_limit_uses_saved_page_count(
    monkeypatch: pytest.MonkeyPatch, pages: int, allowed: bool
) -> None:
    metadata = SimpleNamespace(num_pages=pages)
    monkeypatch.setattr(olefile.OleFileIO, "get_metadata", lambda self: metadata)

    if allowed:
        assert extract_text(REAL_DOC.read_bytes(), [])[0] == "doc"
    else:
        with pytest.raises(ResumeExtractionError, match=TOO_MANY_PAGES_MESSAGE):
            extract_text(REAL_DOC.read_bytes(), [])


@pytest.mark.skipif(not REAL_DOC.exists(), reason="Word 97 sample file not on this machine")
@pytest.mark.parametrize(
    ("offset", "value", "message"),
    [(0x0A, 0x0100, PASSWORD_MESSAGE), (0x02, 0x0065, OLD_WORD_MESSAGE)],
    ids=["fEncrypted", "word-6-nfib"],
)
def test_real_doc_with_patched_fib_is_rejected(offset: int, value: int, message: str) -> None:
    data = bytearray(REAL_DOC.read_bytes())
    fib = data.find(b"\xec\xa5\xc1\x00")  # wIdent 0xA5EC, nFib 193
    if offset == 0x0A:
        value |= struct.unpack_from("<H", data, fib + offset)[0]
    struct.pack_into("<H", data, fib + offset, value)

    with pytest.raises(ResumeExtractionError) as error:
        extract_text(bytes(data), [])
    assert str(error.value) == message


# --- Rejections ---


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        b"{\\rtf1\\ansi Jane Doe}",
        build_zip({"xl/workbook.xml": "<workbook/>"}),
        build_ole("Workbook"),
    ],
    ids=["png", "rtf", "zip-without-document-xml", "ole-without-word-document"],
)
def test_unsupported_types_are_rejected(data: bytes) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        extract_text(data, [])


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (build_ole("EncryptedPackage"), PASSWORD_MESSAGE),
        (b"", EMPTY_MESSAGE),
        (b"  \r\n\t ", EMPTY_MESSAGE),
        (b"%PDF-1.4 not really a pdf", UNREADABLE_MESSAGE),
        (b"PK\x03\x04 not really a zip", UNREADABLE_MESSAGE),
    ],
    ids=["encrypted-ole", "empty-file", "whitespace-only", "damaged-pdf", "damaged-zip"],
)
def test_unusable_files_are_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ResumeExtractionError) as error:
        extract_text(data, [])
    assert str(error.value) == message


def test_get_resume_intake_returns_the_stored_row(db_session: Session) -> None:
    answers = InterviewAnswers(
        notice_period="2 weeks",
        salary_expectation="$60k/year",
        work_mode="hybrid",
        rating=4,
        strengths="Clear communicator",
        concerns="Little cloud experience",
        overall_notes="Move to the next round",
    )
    saved = save_resume_intake(
        db_session, "cv.txt", "txt", "Jane Doe", FAKE_RESUME_DATA, answers, []
    )

    intake = get_resume_intake(db_session, saved.id)

    assert intake is not None
    assert intake.id == saved.id
    assert intake.structured_data == FAKE_RESUME_DATA.model_dump()


def test_get_resume_intake_returns_none_for_an_unknown_id(db_session: Session) -> None:
    assert get_resume_intake(db_session, -1) is None

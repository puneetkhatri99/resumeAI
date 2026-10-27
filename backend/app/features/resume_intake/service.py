"""Resume intake business logic: read the text out of a CV file, store it with the details
AI extracted from it (resume_structuring) and the answers, and read a stored intake back.

Supported CV types, detected by content (never by filename): PDF, DOCX, legacy Word 97+ DOC
and plain text (HTML saved as .doc counts as text). A scanned PDF is read by OCR
(resume_structuring.transcribe_pdf). CV text is never logged.
"""

import codecs
import ctypes
import io
import logging
import re
import struct
import threading
import zipfile
from collections.abc import Callable, Collection
from xml.etree import ElementTree

import olefile
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from sqlalchemy import Row, select
from sqlalchemy.orm import Session

from app.features.ai_usage.service import AiCall, add_ai_calls, intake_cost_column
from app.features.resume_intake.models import ResumeIntake
from app.features.resume_intake.schemas import FileType, InterviewAnswers, clean_text
from app.features.resume_structuring.service import (
    MissingContactError,
    ResumeData,
    extract_resume_data,
    transcribe_pdf,
)

logger = logging.getLogger(__name__)

MAX_PAGES = 4
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024

UNSUPPORTED_TYPE_MESSAGE = "Unsupported file type. Upload the CV as a PDF, DOCX, DOC or TXT file."
TOO_MANY_PAGES_MESSAGE = (
    f"The CV is over {MAX_PAGES} pages. Upload a CV of at most {MAX_PAGES} pages."
)
PASSWORD_MESSAGE = "The CV is password-protected. Remove the password and upload it again."
UNREADABLE_MESSAGE = "The CV file is damaged or unreadable."
EMPTY_MESSAGE = "No text was found in the CV."
OLD_WORD_MESSAGE = "Word 6/95 .doc files aren't supported. Save the CV as .docx or PDF and retry."
DOCX_TOO_LARGE_MESSAGE = "The DOCX file is too large to read."

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

# Word 97 binary format ([MS-DOC]): FIB fields and piece-table bits.
WORD_IDENT = 0xA5EC
WORD_97_NFIB = 193
FIB_FLAGS_OFFSET = 0x0A
FIB_FLAG_ENCRYPTED = 0x0100
FIB_FLAG_TABLE_1 = 0x0200
FIB_CLX_OFFSET = 0x1A2
PIECE_FC_MASK = 0x3FFFFFFF
PIECE_COMPRESSED = 0x40000000
DOC_CHAR_MAP = str.maketrans({"\r": "\n", "\x0b": "\n", "\x0c": "\n", "\x07": "\n", "\x1e": "-"})

DOCX_HEADER_PART = re.compile(r"word/header\d*\.xml")
DOCX_FOOTER_PART = re.compile(r"word/footer\d*\.xml")
# A PDF link annotation's address counts as a web link when it starts like this.
WEB_URL = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
# read_pdf puts the link addresses after this; structure_resume keeps them when it OCRs.
LINKS_HEADING = "\n\nLinks in the PDF:\n"
# pPr holds tab-stop definitions (not text); Fallback repeats the text box shown by Choice.
DOCX_SKIPPED_TAGS = {"pPr", "Fallback"}

# PDFium is not thread-safe, and sync endpoints run in a thread pool.
PDFIUM_LOCK = threading.Lock()


class UnsupportedFileTypeError(Exception):
    """The CV is not a PDF, DOCX, DOC or TXT file, judged by its content (HTTP 415)."""


class ResumeExtractionError(Exception):
    """The CV has a supported type but no usable text can be read from it (HTTP 422)."""


def save_resume_intake(
    db: Session,
    filename: str,
    file_type: FileType,
    text: str,
    structured_data: ResumeData,
    answers: InterviewAnswers,
    ai_calls: list[AiCall],
) -> ResumeIntake:
    """Store the CV's text and structured data with the cleaned filename and the answers, and
    the upload's AI calls linked to it, in one commit.

    Only stores: the text comes from extract_text and the structured data from
    resume_structuring, so nothing is written when either of those fails.
    """
    intake = ResumeIntake(
        original_filename=clean_text(filename),
        file_type=file_type,
        extracted_text=text,
        structured_data=structured_data.model_dump(),
        **answers.model_dump(),
    )
    db.add(intake)
    db.flush()  # gives the intake its id, for the calls' link
    add_ai_calls(db, ai_calls, intake.id)
    db.commit()
    db.refresh(intake)
    return intake


def get_resume_intake(db: Session, intake_id: int) -> ResumeIntake | None:
    """Return the stored intake with this id, or None. Other features read intakes only
    through this function (rule 7); e.g. resume_generation reads its structured_data.
    """
    return db.get(ResumeIntake, intake_id)


def list_resume_intakes(db: Session, before_id: int | None, limit: int) -> tuple[list[Row], bool]:
    """Return up to `limit` intakes, newest first, and whether older ones exist.

    With `before_id`, only intakes with a smaller id are listed: paging by id, so a submit made
    while someone pages can't shift rows into duplicates. Only the columns the past-interviews
    list shows are read (never the CV text); the name and email come from structured_data and
    are None for rows stored before AI extraction existed. ai_cost_usd sums the intake's AI
    calls (ai_usage); it is None for intakes from before usage was recorded.
    """
    query = (
        select(
            ResumeIntake.id,
            ResumeIntake.created_at,
            ResumeIntake.original_filename,
            ResumeIntake.rating,
            ResumeIntake.work_mode,
            ResumeIntake.structured_data["name"].astext.label("candidate_name"),
            ResumeIntake.structured_data["email"].astext.label("candidate_email"),
            intake_cost_column(ResumeIntake.id).label("ai_cost_usd"),
        )
        .order_by(ResumeIntake.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        query = query.where(ResumeIntake.id < before_id)
    rows = list(db.execute(query).all())
    return rows[:limit], len(rows) > limit


def list_candidate_names(db: Session, intake_ids: Collection[int]) -> dict[int, str | None]:
    """Return the AI-extracted candidate name of each of these intakes, by id.

    Ids with no intake are left out; the name is None for intakes from before AI extraction.
    The AI usage panel (ai_usage) names its recent calls with it.
    """
    query = select(ResumeIntake.id, ResumeIntake.structured_data["name"].astext).where(
        ResumeIntake.id.in_(intake_ids)
    )
    return dict(db.execute(query).all())


def extract_text(data: bytes, ai_calls: list[AiCall]) -> tuple[FileType, str, bool]:
    """Detect the CV's type from its bytes and return (file_type, cleaned text, read_by_ocr).

    A PDF with no text layer (a scan) is read by OCR, so read_by_ocr is True; its page and
    password checks run first, which caps what OCR can cost. The OCR call is recorded in
    `ai_calls`. Any unexpected parser failure becomes ResumeExtractionError; only its type is
    logged. An OCR failure stays an AiExtractionError (HTTP 502).
    """
    reader: Callable[[bytes], str]
    if data.startswith(OLE_MAGIC):
        file_type, reader = "doc", read_doc
    elif data.startswith(ZIP_MAGIC):
        file_type, reader = "docx", read_docx
    elif data.startswith(b"{\\rtf"):
        raise UnsupportedFileTypeError(UNSUPPORTED_TYPE_MESSAGE)
    elif b"%PDF-" in data[:1024]:
        file_type, reader = "pdf", read_pdf
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or b"\x00" not in data:
        file_type, reader = "txt", read_txt
    else:
        raise UnsupportedFileTypeError(UNSUPPORTED_TYPE_MESSAGE)

    try:
        text = reader(data)
    except (UnsupportedFileTypeError, ResumeExtractionError):
        raise
    except Exception as exc:
        logger.warning("Could not read a %s CV: %s", file_type, type(exc).__name__)
        raise ResumeExtractionError(UNREADABLE_MESSAGE) from None

    text = clean_text(text)
    read_by_ocr = file_type == "pdf" and not text
    if read_by_ocr:
        # Outside the try (so it isn't "unreadable") and outside PDFIUM_LOCK (OpenAI is slow).
        text = clean_text(transcribe_pdf(data, ai_calls, "ocr"))
    if not text:
        raise ResumeExtractionError(EMPTY_MESSAGE)
    return file_type, text, read_by_ocr


def structure_resume(
    data: bytes, file_type: FileType, text: str, read_by_ocr: bool, ai_calls: list[AiCall]
) -> tuple[str, ResumeData]:
    """Have AI structure the CV text; return (the text it structured, the structured data).

    If the AI finds no name or email in a PDF read from its text layer, the PDF is read once
    more by OCR and structured again: the name or email may be in an image header, or the text
    layer may be garbled (a broken font encoding) or hold only a watermark. The "Links in the
    PDF" block is kept, because OCR can't see link annotations. The returned text is the one
    that was structured, so the stored text matches the stored data. A second miss is a 422.
    Every AI call made here is recorded in `ai_calls`.
    """
    try:
        return text, extract_resume_data(text, ai_calls)
    except MissingContactError:
        if file_type != "pdf" or read_by_ocr:
            raise
        ocr_text = clean_text(transcribe_pdf(data, ai_calls, "ocr_retry"))
        if not ocr_text:
            raise
    _, links_heading, links = text.partition(LINKS_HEADING)
    ocr_text += links_heading + links
    return ocr_text, extract_resume_data(ocr_text, ai_calls)


def read_pdf(data: bytes) -> str:
    """Return the text of every page, separated by blank lines, or "" when the PDF has no text
    layer (a scan, which extract_text then OCRs). Rejects CVs over MAX_PAGES.
    """
    with PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if exc.err_code == pdfium_c.FPDF_ERR_PASSWORD:
                raise ResumeExtractionError(PASSWORD_MESSAGE) from None
            raise
        try:
            if len(document) > MAX_PAGES:
                raise ResumeExtractionError(TOO_MANY_PAGES_MESSAGE)
            page_texts = []
            link_urls = []
            for index in range(len(document)):
                page = document[index]
                text_page = page.get_textpage()
                page_texts.append(text_page.get_text_range())
                text_page.close()
                link_urls += read_pdf_link_urls(document, page)
                page.close()
        finally:
            # Also closes any page or text page left open by an error.
            document.close()

    text = "\n\n".join(page_texts)
    if not text.strip():
        return ""
    # Appended so the AI sees the addresses behind clickable words like "GitHub" (Overleaf CVs).
    unique_urls = [url for url in dict.fromkeys(link_urls) if url not in text]
    if unique_urls:
        text += LINKS_HEADING + "\n".join(unique_urls)
    return text


def read_pdf_link_urls(document: pdfium.PdfDocument, page: pdfium.PdfPage) -> list[str]:
    """Return the web addresses of the page's link annotations. Call it while holding PDFIUM_LOCK.

    A LaTeX CV usually shows a link as a word ("LinkedIn") or an icon, so its address is only
    in the annotation, never in the text layer. mailto:, tel: and in-document links are skipped.
    """
    urls = []
    position = ctypes.c_int(0)
    link = pdfium_c.FPDF_LINK()
    while pdfium_c.FPDFLink_Enumerate(page.raw, ctypes.byref(position), ctypes.byref(link)):
        action = pdfium_c.FPDFLink_GetAction(link)
        if not action or pdfium_c.FPDFAction_GetType(action) != pdfium_c.PDFACTION_URI:
            continue
        size = pdfium_c.FPDFAction_GetURIPath(document.raw, action, None, 0)
        buffer = ctypes.create_string_buffer(size)
        pdfium_c.FPDFAction_GetURIPath(document.raw, action, buffer, size)
        url = clean_text(buffer.value.decode("latin-1"))
        if WEB_URL.match(url):
            urls.append(url)
    return urls


def read_docx(data: bytes) -> str:
    """Return the text of the headers, the body and the footers, in that order.

    Uses only the standard library. The uncompressed size of the parts is checked before
    they are read, and the page count Word saved in docProps/app.xml is enforced when present.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise UnsupportedFileTypeError(UNSUPPORTED_TYPE_MESSAGE)
        parts = [
            *sorted(name for name in names if DOCX_HEADER_PART.fullmatch(name)),
            "word/document.xml",
            *sorted(name for name in names if DOCX_FOOTER_PART.fullmatch(name)),
        ]
        app_part = ["docProps/app.xml"] if "docProps/app.xml" in names else []
        # zipfile stops at the declared size, so this also bounds a zip bomb.
        if sum(archive.getinfo(name).file_size for name in parts + app_part) > MAX_DOCX_XML_BYTES:
            raise ResumeExtractionError(DOCX_TOO_LARGE_MESSAGE)

        if app_part:
            properties = ElementTree.fromstring(archive.read("docProps/app.xml"))
            pages = next((el.text for el in properties if local_name(el) == "Pages"), None)
            if pages is not None and int(pages) > MAX_PAGES:
                raise ResumeExtractionError(TOO_MANY_PAGES_MESSAGE)

        texts: list[str] = []
        for name in parts:
            collect_docx_text(ElementTree.fromstring(archive.read(name)), texts)
    return "".join(texts)


def local_name(element: ElementTree.Element) -> str:
    """Return the tag without its namespace, so both transitional and strict OOXML match."""
    return element.tag.rsplit("}", 1)[-1]


def collect_docx_text(element: ElementTree.Element, texts: list[str]) -> None:
    """Append the visible text under `element` to `texts`, in document order."""
    tag = local_name(element)
    if tag in DOCX_SKIPPED_TAGS:
        return
    if tag == "t":
        texts.append(element.text or "")
    elif tag == "tab":
        texts.append("\t")
    elif tag in ("br", "cr"):
        texts.append("\n")
    elif tag == "noBreakHyphen":
        texts.append("-")
    for child in element:
        collect_docx_text(child, texts)
    if tag == "p":
        texts.append("\n")


def read_doc(data: bytes) -> str:
    """Return the text of a Word 97+ .doc. Rejects encrypted, Word 6/95 and long documents."""
    # Always a file object: olefile treats raw bytes shorter than 1536 as a file path.
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        if ole.exists("EncryptedPackage"):
            raise ResumeExtractionError(PASSWORD_MESSAGE)
        if not ole.exists("WordDocument"):
            raise UnsupportedFileTypeError(UNSUPPORTED_TYPE_MESSAGE)
        pages = ole.get_metadata().num_pages
        if pages is not None and pages > MAX_PAGES:
            raise ResumeExtractionError(TOO_MANY_PAGES_MESSAGE)

        word_stream = ole.openstream("WordDocument").read()
        ident, nfib = struct.unpack_from("<HH", word_stream, 0)
        (flags,) = struct.unpack_from("<H", word_stream, FIB_FLAGS_OFFSET)
        if ident != WORD_IDENT:
            raise ResumeExtractionError(UNREADABLE_MESSAGE)
        if nfib < WORD_97_NFIB:
            raise ResumeExtractionError(OLD_WORD_MESSAGE)
        if flags & FIB_FLAG_ENCRYPTED:
            raise ResumeExtractionError(PASSWORD_MESSAGE)
        table_stream = ole.openstream("1Table" if flags & FIB_FLAG_TABLE_1 else "0Table").read()
    return read_doc_streams(word_stream, table_stream)


def read_doc_streams(word_stream: bytes, table_stream: bytes) -> str:
    """Return the text of every story in a Word 97 document by walking its piece table.

    The piece table sits in the CLX (in the table stream), after any Prc blocks. Each piece
    is either cp1252 ("compressed") or UTF-16LE text in the WordDocument stream.
    """
    fc_clx, lcb_clx = struct.unpack_from("<II", word_stream, FIB_CLX_OFFSET)
    clx = table_stream[fc_clx : fc_clx + lcb_clx]
    position = 0
    while position < len(clx) and clx[position] == 0x01:
        # Unsigned, so a huge value moves forward and the loop always ends.
        (cb_grpprl,) = struct.unpack_from("<H", clx, position + 1)
        position += 3 + cb_grpprl
    if position >= len(clx) or clx[position] != 0x02:
        raise ResumeExtractionError(UNREADABLE_MESSAGE)

    (lcb,) = struct.unpack_from("<I", clx, position + 1)
    plc_pcd = clx[position + 5 : position + 5 + lcb]
    piece_count = max((len(plc_pcd) - 4) // 12, 0)
    cps = struct.unpack_from(f"<{piece_count + 1}I", plc_pcd, 0) if piece_count else ()
    pcds_offset = 4 * (piece_count + 1)

    texts = []
    bytes_read = 0
    for index in range(piece_count):
        (raw_fc,) = struct.unpack_from("<I", plc_pcd, pcds_offset + 8 * index + 2)
        char_count = cps[index + 1] - cps[index]
        fc = raw_fc & PIECE_FC_MASK
        if raw_fc & PIECE_COMPRESSED:
            chunk = word_stream[fc // 2 : fc // 2 + char_count]
            texts.append(chunk.decode("cp1252", errors="replace"))
        else:
            chunk = word_stream[fc : fc + 2 * char_count]
            texts.append(chunk.decode("utf-16-le", errors="replace"))
        bytes_read += len(chunk)
        # Pieces that overlap or repeat could otherwise multiply the output size.
        if bytes_read > len(word_stream):
            raise ResumeExtractionError(UNREADABLE_MESSAGE)
    return clean_doc_text("".join(texts))


def clean_doc_text(text: str) -> str:
    """Turn Word's special characters into plain text and keep only fields' visible results.

    A field is \\x13 instruction \\x14 result \\x15 (the result part is optional). Fields can
    nest, so open fields are tracked on a stack; unmatched \\x14 or \\x15 markers are ignored.
    """
    text = text.translate(DOC_CHAR_MAP)
    kept: list[str] = []
    in_instruction: list[bool] = []
    hidden_depth = 0
    for char in text:
        if char == "\x13":
            in_instruction.append(True)
            hidden_depth += 1
        elif char == "\x14":
            if in_instruction and in_instruction[-1]:
                in_instruction[-1] = False
                hidden_depth -= 1
        elif char == "\x15":
            if in_instruction:
                hidden_depth -= in_instruction.pop()
        elif hidden_depth == 0:
            kept.append(char)
    # shortcut: an unclosed \x13 hides the rest of the text; handle it if damaged files show up.
    return "".join(kept)


def read_txt(data: bytes) -> str:
    """Decode plain text: by its BOM if it has one, else UTF-8, else cp1252."""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")

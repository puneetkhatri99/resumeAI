"""Fixtures for the resume_intake tests: a builder for small hand-written PDFs."""

from collections.abc import Callable

import pytest

# Standard security handler with a user password: O and U are arbitrary, so the empty password
# PDFium tries first never matches and the file counts as password-protected.
ENCRYPT_TRAILER = (
    "/Encrypt << /Filter /Standard /V 1 /R 2 /O <" + "11" * 32 + "> /U <" + "22" * 32 + "> /P -4 >>"
    " /ID [<00112233445566778899aabbccddeeff> <00112233445566778899aabbccddeeff>]"
)


def build_pdf(
    page_texts: list[str], encrypted: bool = False, link_urls: tuple[str, ...] = ()
) -> bytes:
    """Return a PDF with one page per entry, each showing that text (an empty string = blank page).

    With `encrypted`, the PDF is password-protected. Each of `link_urls` becomes a link annotation
    on the first page whose address appears nowhere in the text, like "GitHub" in a LaTeX CV.

    The xref offsets are computed, so the file is valid, not just tolerated by PDFium.
    """
    page_count = len(page_texts)
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(page_count))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    annotations = " ".join(
        f"<< /Type /Annot /Subtype /Link /Rect [72 {700 - 20 * number} 200 {715 - 20 * number}]"
        f" /A << /S /URI /URI ({url}) >> >>"
        for number, url in enumerate(link_urls)
    )
    for index, text in enumerate(page_texts):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        page_annotations = f" /Annots [{annotations}]" if annotations and index == 0 else ""
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            f" /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R"
            f"{page_annotations} >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")

    pdf = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    pdf += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    encryption = ENCRYPT_TRAILER if encrypted else ""
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {encryption} >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return pdf


@pytest.fixture
def make_pdf() -> Callable[..., bytes]:
    """The build_pdf function, as a fixture so both test modules can use it."""
    return build_pdf

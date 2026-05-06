"""PDF page / region rendering via pymupdf.

Used by the ``/image`` HTTP endpoint and the ``texwatch_image`` MCP tool
to give Claude a visual on the rendered output.  Rendering is the one
job the agent genuinely can't do for itself: PDF rendering nuances
(figure placement, equation typesetting, overfull-box wrap, table
spacing) only show up in the actual PDF.

pymupdf is an optional dependency (``pip install texwatch[image]``).
We surface a friendly message when it's missing rather than crashing.
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    import fitz  # pymupdf

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from .synctex import SyncTeXData

logger = logging.getLogger(__name__)


class ImagingError(RuntimeError):
    """Raised when rendering can't proceed (missing dep, bad inputs, etc)."""


def _check_dep() -> None:
    if not HAS_PYMUPDF:
        raise ImagingError(
            "PDF imaging requires pymupdf. "
            "Install with: pip install 'texwatch[image]'"
        )


# ---------------------------------------------------------------------------
# Page / region rendering
# ---------------------------------------------------------------------------


def render_page(pdf_path: Path, page: int, dpi: int = 150) -> bytes:
    """Render an entire page as PNG bytes.

    Args:
        pdf_path: Path to compiled PDF.
        page: 1-indexed page number.
        dpi: Render resolution.  150 is roughly retina-equivalent for
            on-screen display; 300 for high-detail extraction.

    Raises:
        ImagingError: pymupdf missing, or *page* out of range.
    """
    _check_dep()
    if not pdf_path.exists():
        raise ImagingError(f"PDF not found: {pdf_path}")
    doc = fitz.open(pdf_path)
    try:
        if page < 1 or page > len(doc):
            raise ImagingError(f"page {page} out of range (1..{len(doc)})")
        pix = doc[page - 1].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def render_region(
    pdf_path: Path,
    page: int,
    bbox: tuple[float, float, float, float],
    dpi: int = 150,
    margin: float = 6.0,
) -> bytes:
    """Render a rectangular region of a page as PNG bytes.

    *bbox* is in PDF points (1/72 inch), top-left origin (matching the
    coordinate system in ``PdfRegionAnchor`` and PDF.js viewports).
    A small *margin* is added around the crop so the rendered region
    has visible breathing room.
    """
    _check_dep()
    if not pdf_path.exists():
        raise ImagingError(f"PDF not found: {pdf_path}")
    doc = fitz.open(pdf_path)
    try:
        if page < 1 or page > len(doc):
            raise ImagingError(f"page {page} out of range (1..{len(doc)})")
        p = doc[page - 1]
        x1, y1, x2, y2 = bbox
        # Clip to page bounds; pymupdf will raise on out-of-page rects.
        rect = fitz.Rect(
            max(0.0, x1 - margin),
            max(0.0, y1 - margin),
            min(p.rect.width, x2 + margin),
            min(p.rect.height, y2 + margin),
        )
        if rect.is_empty or rect.is_infinite:
            raise ImagingError(f"empty bbox after clip: {tuple(bbox)} on {p.rect}")
        pix = p.get_pixmap(dpi=dpi, clip=rect)
        return pix.tobytes("png")
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# SyncTeX -> region resolution
# ---------------------------------------------------------------------------


def resolve_source_to_region(
    synctex: SyncTeXData,
    file: str,
    line_start: int,
    line_end: int,
    pad: float = 4.0,
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Map a source line range to ``(page, bbox)`` using SyncTeX.

    Strategy:
      - Collect every SyncTeX position for ``(file, line)`` in the
        requested range.
      - Group by page; pick the page with the most matches.
      - Compute the bounding box of matched positions on that page,
        using the per-position width/height SyncTeX records.
      - Add *pad* on each side for breathing room.

    Returns None if nothing matched (e.g., the range falls inside a
    figure environment, which has no SyncTeX coverage).
    """
    by_page: dict[int, list] = {}
    for line in range(line_start, line_end + 1):
        for pos in synctex.source_to_pdf.get((file, line), []):
            by_page.setdefault(pos.page, []).append(pos)

    if not by_page:
        return None

    best_page = max(by_page, key=lambda k: len(by_page[k]))
    positions = by_page[best_page]

    x1 = min(p.x for p in positions)
    x2 = max(p.x + max(p.width, 0) for p in positions)
    # SyncTeX y is the baseline; height extends upward (toward smaller y
    # in top-left coord systems).  Using the recorded height covers the
    # ascender; we approximate descender with the same pad value.
    y1 = min(p.y - max(p.height, 0) for p in positions)
    y2 = max(p.y for p in positions)

    return best_page, (x1 - pad, y1 - pad, x2 + pad, y2 + pad)

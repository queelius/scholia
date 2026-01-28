"""SyncTeX file parsing for source/PDF position mapping.

SyncTeX enables bidirectional mapping between:
- Source positions (file, line, column) -> PDF positions (page, x, y)
- PDF positions (page, x, y) -> Source positions (file, line, column)
"""

import gzip
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SourcePosition:
    """Position in a source file."""

    file: str
    line: int
    column: int = 0


@dataclass
class PDFPosition:
    """Position in PDF."""

    page: int
    x: float  # horizontal position (points)
    y: float  # vertical position (points)
    width: float = 0.0
    height: float = 0.0


@dataclass
class SyncTeXData:
    """Parsed SyncTeX data."""

    # Maps (page, approx_y) -> list of source positions
    pdf_to_source: dict[int, list[tuple[float, SourcePosition]]]
    # Maps (file, line) -> list of PDF positions
    source_to_pdf: dict[tuple[str, int], list[PDFPosition]]
    # List of input files
    input_files: dict[int, str]


def _normalize_path(path: str, base_dir: Path) -> str:
    """Normalize a path relative to base directory."""
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(base_dir))
        except ValueError:
            return str(p)
    return str(p)


def parse_synctex(synctex_path: Path) -> SyncTeXData | None:
    """Parse a SyncTeX file.

    Args:
        synctex_path: Path to .synctex.gz or .synctex file.

    Returns:
        SyncTeXData with parsed mappings, or None if parsing fails.
    """
    base_dir = synctex_path.parent

    try:
        if synctex_path.suffix == ".gz" or str(synctex_path).endswith(".synctex.gz"):
            with gzip.open(synctex_path, "rt", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            with open(synctex_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
    except (OSError, gzip.BadGzipFile):
        return None

    input_files: dict[int, str] = {}
    pdf_to_source: dict[int, list[tuple[float, SourcePosition]]] = {}
    source_to_pdf: dict[tuple[str, int], list[PDFPosition]] = {}

    current_page = 0
    current_file_id = 0

    # Parse input file declarations: Input:N:path
    for match in re.finditer(r"^Input:(\d+):(.+)$", content, re.MULTILINE):
        file_id = int(match.group(1))
        file_path = _normalize_path(match.group(2).strip(), base_dir)
        input_files[file_id] = file_path

    # Parse page markers
    page_pattern = re.compile(r"^\{(\d+)$", re.MULTILINE)

    # Parse position records
    # Format varies but common patterns:
    # h<file_id>,<line>,<column>:<x>,<y>:<width>,<height>
    # v<file_id>,<line>,<column>:<x>,<y>:<width>,<height>
    # x<file_id>,<line>,<column>:<x>,<y>
    # k<file_id>,<line>,<column>:<x>,<y>:<width>,<height>
    # g<file_id>,<line>,<column>:<x>,<y>
    # $<file_id>,<line>,<column>:<x>,<y>

    record_pattern = re.compile(
        r"^([hvxkg$\[\]])(\d+),(\d+),(-?\d+):(-?\d+),(-?\d+)(?::(-?\d+),(-?\d+))?",
        re.MULTILINE,
    )

    lines = content.split("\n")
    for line in lines:
        # Check for page marker
        if line.startswith("{"):
            try:
                current_page = int(line[1:])
                if current_page not in pdf_to_source:
                    pdf_to_source[current_page] = []
            except ValueError:
                pass
            continue

        # Parse position record
        match = record_pattern.match(line)
        if match:
            record_type = match.group(1)
            file_id = int(match.group(2))
            line_num = int(match.group(3))
            column = int(match.group(4))
            x = int(match.group(5))
            y = int(match.group(6))
            width = int(match.group(7)) if match.group(7) else 0
            height = int(match.group(8)) if match.group(8) else 0

            # Convert from scaled points to points (65536 sp = 1 pt)
            x_pt = x / 65536.0
            y_pt = y / 65536.0
            width_pt = width / 65536.0
            height_pt = height / 65536.0

            file_path = input_files.get(file_id, f"file_{file_id}")

            # Add to pdf_to_source
            if current_page > 0:
                source_pos = SourcePosition(file=file_path, line=line_num, column=column)
                pdf_to_source[current_page].append((y_pt, source_pos))

            # Add to source_to_pdf
            key = (file_path, line_num)
            pdf_pos = PDFPosition(
                page=current_page, x=x_pt, y=y_pt, width=width_pt, height=height_pt
            )
            if key not in source_to_pdf:
                source_to_pdf[key] = []
            source_to_pdf[key].append(pdf_pos)

    return SyncTeXData(
        pdf_to_source=pdf_to_source,
        source_to_pdf=source_to_pdf,
        input_files=input_files,
    )


def find_synctex_file(pdf_path: Path) -> Path | None:
    """Find the SyncTeX file for a PDF.

    Args:
        pdf_path: Path to PDF file.

    Returns:
        Path to SyncTeX file if found, None otherwise.
    """
    base = pdf_path.with_suffix("")

    # Try common extensions
    for ext in [".synctex.gz", ".synctex"]:
        synctex_path = base.parent / (base.name + ext)
        if synctex_path.exists():
            return synctex_path

    return None


def source_to_page(data: SyncTeXData, file: str, line: int) -> PDFPosition | None:
    """Find PDF position for a source line.

    Args:
        data: Parsed SyncTeX data.
        file: Source file name.
        line: Line number (1-indexed).

    Returns:
        PDF position or None if not found.
    """
    # Try exact match first
    key = (file, line)
    if key in data.source_to_pdf:
        positions = data.source_to_pdf[key]
        if positions:
            return positions[0]

    # Try finding nearest line in same file
    matching_keys = [(f, l) for f, l in data.source_to_pdf.keys() if f == file]
    if matching_keys:
        # Find closest line
        closest = min(matching_keys, key=lambda k: abs(k[1] - line))
        positions = data.source_to_pdf[closest]
        if positions:
            return positions[0]

    return None


def page_to_source(data: SyncTeXData, page: int, y: float | None = None) -> SourcePosition | None:
    """Find source position for a PDF page (and optional y coordinate).

    Args:
        data: Parsed SyncTeX data.
        page: Page number (1-indexed).
        y: Optional y coordinate in points.

    Returns:
        Source position or None if not found.
    """
    if page not in data.pdf_to_source:
        return None

    positions = data.pdf_to_source[page]
    if not positions:
        return None

    if y is None:
        # Return first position on page
        return positions[0][1]

    # Find closest y position
    closest = min(positions, key=lambda p: abs(p[0] - y))
    return closest[1]


def get_visible_lines(data: SyncTeXData, page: int) -> tuple[int, int] | None:
    """Get the range of source lines visible on a page.

    Args:
        data: Parsed SyncTeX data.
        page: Page number (1-indexed).

    Returns:
        Tuple of (start_line, end_line) or None if not found.
    """
    if page not in data.pdf_to_source:
        return None

    positions = data.pdf_to_source[page]
    if not positions:
        return None

    lines = [pos[1].line for pos in positions]
    return (min(lines), max(lines))

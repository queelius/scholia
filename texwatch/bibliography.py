"""BibTeX bibliography and citation parsing.

Parses .bib files for bibliography entries and .tex files for citation
commands.  Cross-references the two to identify uncited and undefined keys.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .structure import _extract_braced, _find_tex_files, _read_files, _strip_comment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BibEntry:
    """A single entry in a .bib file.

    Attributes:
        key: Citation key (e.g. "knuth1984").
        entry_type: Entry type (e.g. "article", "book").
        fields: Parsed fields mapping (e.g. {"title": "...", "author": "..."}).
        file: Bib file path relative to watch_dir.
        line: Line number of the @type{key, declaration (1-indexed).
    """

    key: str
    entry_type: str
    fields: dict[str, str] = field(default_factory=dict)
    file: str = ""
    line: int = 0


@dataclass
class Citation:
    """A citation command found in a .tex file.

    Attributes:
        command: The citation command name (e.g. "cite", "citep", "citet").
        keys: List of citation keys referenced.
        file: Source file path relative to watch_dir.
        line: Line number (1-indexed).
    """

    command: str
    keys: list[str] = field(default_factory=list)
    file: str = ""
    line: int = 0


@dataclass
class Bibliography:
    """Aggregated bibliography analysis for a LaTeX project.

    Attributes:
        entries: All BibTeX entries found in .bib files.
        citations: All citation commands found in .tex files.
        uncited_keys: Keys defined in .bib but never cited in .tex.
        undefined_keys: Keys cited in .tex but not defined in any .bib.
    """

    entries: list[BibEntry] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    uncited_keys: list[str] = field(default_factory=list)
    undefined_keys: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches @article{key, or @book{key, etc.  Group 1: type, Group 2: key.
_BIB_ENTRY_START_RE = re.compile(
    r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.IGNORECASE
)

# Matches a bib field name and equals sign: author = (value follows)
_BIB_FIELD_NAME_RE = re.compile(r"(\w+)\s*=\s*", re.IGNORECASE)

# Matches \cite{...}, \citep{...}, \citet{...}, \citeauthor{...},
# \citeyear{...}, \nocite{...} with optional [...] arguments.
_CITE_RE = re.compile(
    r"\\(cite[pt]?|citeauthor|citeyear|nocite)"
    r"(?:\[[^\]]*\])*"
    r"\{([^}]+)\}"
)


# ---------------------------------------------------------------------------
# Bib file parsing
# ---------------------------------------------------------------------------


def _parse_bib_file(content: str, rel_path: str) -> list[BibEntry]:
    """Parse BibTeX entries from file content.

    Uses regex to find entry starts and brace-depth counting to find
    entry boundaries.  Fields are extracted with a simpler regex.
    """
    entries: list[BibEntry] = []
    lines = content.splitlines()

    for line_no, line in enumerate(lines, start=1):
        for m in _BIB_ENTRY_START_RE.finditer(line):
            entry_type = m.group(1).lower()

            # Skip @string, @preamble, @comment
            if entry_type in ("string", "preamble", "comment"):
                continue

            key = m.group(2)

            # Collect the full entry text using brace-depth counting
            # starting from the opening brace in the match.
            start_col = m.start()
            depth = 0
            entry_text_lines: list[str] = []

            for i in range(line_no - 1, len(lines)):
                scan_line = lines[i]
                start = start_col if i == line_no - 1 else 0
                for j in range(start, len(scan_line)):
                    ch = scan_line[j]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            # Include up to closing brace
                            entry_text_lines.append(
                                scan_line[start:j + 1] if i == line_no - 1
                                else scan_line[:j + 1]
                            )
                            break
                else:
                    # Whole line consumed without closing
                    entry_text_lines.append(
                        scan_line[start:] if i == line_no - 1 else scan_line
                    )
                    continue
                break  # found_end or broke out of inner loop

            entry_text = "\n".join(entry_text_lines)

            # Parse fields from the entry text sequentially.
            # We advance past each consumed field value to avoid matching
            # patterns inside already-parsed values (phantom fields).
            fields: dict[str, str] = {}
            scan_pos = 0
            while scan_pos < len(entry_text):
                fm = _BIB_FIELD_NAME_RE.search(entry_text, scan_pos)
                if fm is None:
                    break
                fname = fm.group(1).lower()
                pos = fm.end()
                # Skip whitespace
                while pos < len(entry_text) and entry_text[pos] in " \t\n":
                    pos += 1
                if pos >= len(entry_text):
                    break
                if entry_text[pos] == "{":
                    result = _extract_braced(entry_text, pos)
                    if result:
                        fields[fname] = result[0].strip()
                        scan_pos = result[1]
                    else:
                        scan_pos = pos + 1
                elif entry_text[pos] == '"':
                    end = entry_text.find('"', pos + 1)
                    if end >= 0:
                        fields[fname] = entry_text[pos + 1 : end].strip()
                        scan_pos = end + 1
                    else:
                        scan_pos = pos + 1
                else:
                    scan_pos = pos + 1

            entries.append(BibEntry(
                key=key,
                entry_type=entry_type,
                fields=fields,
                file=rel_path,
                line=line_no,
            ))

    return entries


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------


def _parse_citations(content: str, rel_path: str) -> list[Citation]:
    """Extract citation commands from .tex file content."""
    citations: list[Citation] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        # Skip comment lines
        stripped = raw_line.lstrip()
        if stripped.startswith("%"):
            continue

        line = _strip_comment(raw_line)
        for m in _CITE_RE.finditer(line):
            command = m.group(1)
            raw_keys = m.group(2)
            keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
            citations.append(Citation(
                command=command,
                keys=keys,
                file=rel_path,
                line=line_no,
            ))
    return citations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_bibliography(main_file: Path, watch_dir: Path) -> Bibliography:
    """Parse bibliography data from all .bib and .tex files in *watch_dir*.

    Args:
        main_file: Path to the main .tex file (unused, kept for API consistency).
        watch_dir: Root directory to scan.

    Returns:
        A :class:`Bibliography` with entries, citations, and cross-reference analysis.
    """
    entries: list[BibEntry] = []
    citations: list[Citation] = []

    # Parse .bib files
    bib_files = sorted(watch_dir.rglob("*.bib"))
    for content, rel in _read_files(bib_files, watch_dir, "bibliography"):
        entries.extend(_parse_bib_file(content, rel))

    # Parse .tex files for citations
    tex_files = _find_tex_files(watch_dir)
    for content, rel in _read_files(tex_files, watch_dir, "bibliography"):
        citations.extend(_parse_citations(content, rel))

    # Cross-reference
    bib_keys = {e.key for e in entries}
    cited_keys = {k for c in citations for k in c.keys}

    bib = Bibliography(
        entries=entries,
        citations=citations,
        uncited_keys=sorted(bib_keys - cited_keys),
        undefined_keys=sorted(cited_keys - bib_keys),
    )

    logger.debug(
        "bibliography: %d entries, %d citations, %d uncited, %d undefined",
        len(bib.entries), len(bib.citations),
        len(bib.uncited_keys), len(bib.undefined_keys),
    )

    return bib

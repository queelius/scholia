"""LaTeX document structure parsing.

Parses sections, labels, citations, and \\input/\\include references from
.tex source files.  Used by the ``paper()`` MCP tool and by section-anchor
staleness checks.

Deliberately narrower than the v0.3.0 module: TODO scraping, per-section
statistics, and texcount integration were dropped because v0.4.0 expects
the human to capture review intent as comments rather than embed it in
source.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """A section-level heading (\\section/\\chapter/etc).

    Attributes:
        level: ``chapter``/``section``/``subsection``/``subsubsection``.
        title: Section title.
        file: Source file (relative to watch_dir).
        line: 1-indexed line number.
        label: Closest \\label{...} that follows the heading, or None.
    """

    level: str
    title: str
    file: str
    line: int
    label: str | None = None


@dataclass(frozen=True)
class Label:
    """A \\label{...} declaration."""

    name: str
    file: str
    line: int


@dataclass(frozen=True)
class Citation:
    """A citation key referenced by \\cite/\\citep/\\citet/etc."""

    key: str
    file: str
    line: int


@dataclass(frozen=True)
class InputFile:
    """An \\input/\\include reference."""

    path: str
    file: str
    line: int


@dataclass
class DocumentStructure:
    """Aggregated structure of a LaTeX project."""

    sections: list[Section] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    inputs: list[InputFile] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------


_SECTION_PREFIX_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)\*?(?:\[[^\]]*\])?"
)
_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
_CITE_RE = re.compile(
    r"\\(?:cite[pt]?|citeauthor|citeyear|nocite)(?:\[[^\]]*\])*\{([^}]+)\}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_preceding_backslashes(text: str, pos: int) -> int:
    count = 0
    j = pos - 1
    while j >= 0 and text[j] == "\\":
        count += 1
        j -= 1
    return count


def _extract_braced(text: str, pos: int) -> tuple[str, int] | None:
    """Extract content within matched braces starting at *pos*.

    Returns ``(content, end_pos)`` where ``content`` excludes the outer braces.
    Handles escaped ``\\{`` / ``\\}`` and nested groups.
    """
    if pos >= len(text) or text[pos] != "{":
        return None
    depth = 0
    for i in range(pos, len(text)):
        ch = text[i]
        if ch == "{" and _count_preceding_backslashes(text, i) % 2 == 0:
            depth += 1
        elif ch == "}" and _count_preceding_backslashes(text, i) % 2 == 0:
            depth -= 1
            if depth == 0:
                return text[pos + 1 : i], i + 1
    return None


def _strip_comment(line: str) -> str:
    """Strip an inline LaTeX comment (``%`` and everything after).

    A ``%`` is a comment marker only when preceded by an even number of
    backslashes.
    """
    i = 0
    while i < len(line):
        if line[i] == "%" and _count_preceding_backslashes(line, i) % 2 == 0:
            return line[:i]
        i += 1
    return line


def _find_tex_files(watch_dir: Path) -> list[Path]:
    return sorted(watch_dir.rglob("*.tex"))


def _relative(path: Path, watch_dir: Path) -> str:
    try:
        return str(path.relative_to(watch_dir))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_sections(content: str, rel_path: str) -> list[Section]:
    """Extract section headings (and the immediately-following label, if any)."""
    sections: list[Section] = []
    lines = content.splitlines()
    n = len(lines)

    for line_no, raw_line in enumerate(lines, start=1):
        if raw_line.lstrip().startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _SECTION_PREFIX_RE.finditer(line):
            pos = m.end()
            while pos < len(line) and line[pos] in " \t":
                pos += 1
            extracted = _extract_braced(line, pos)
            if not extracted:
                continue
            title = extracted[0].strip()

            # Look ahead a few lines for \label{...}
            label: str | None = None
            for ahead in range(line_no - 1, min(line_no + 3, n)):
                candidate = _strip_comment(lines[ahead])
                lm = _LABEL_RE.search(candidate)
                if lm:
                    label = lm.group(1).strip()
                    break

            sections.append(
                Section(
                    level=m.group(1),
                    title=title,
                    file=rel_path,
                    line=line_no,
                    label=label,
                )
            )
    return sections


def _parse_labels(content: str, rel_path: str) -> list[Label]:
    labels: list[Label] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        if raw_line.lstrip().startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _LABEL_RE.finditer(line):
            labels.append(Label(name=m.group(1).strip(), file=rel_path, line=line_no))
    return labels


def _parse_citations(content: str, rel_path: str) -> list[Citation]:
    citations: list[Citation] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        if raw_line.lstrip().startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _CITE_RE.finditer(line):
            for key in m.group(1).split(","):
                key = key.strip()
                if key:
                    citations.append(
                        Citation(key=key, file=rel_path, line=line_no)
                    )
    return citations


def _parse_inputs(content: str, rel_path: str) -> list[InputFile]:
    inputs: list[InputFile] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        if raw_line.lstrip().startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _INPUT_RE.finditer(line):
            inputs.append(
                InputFile(path=m.group(1).strip(), file=rel_path, line=line_no)
            )
    return inputs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_structure(watch_dir: Path) -> DocumentStructure:
    """Parse structure across every .tex file under *watch_dir*."""
    structure = DocumentStructure()

    for path in _find_tex_files(watch_dir):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("structure: failed to read %s", path)
            continue
        rel = _relative(path, watch_dir)
        structure.sections.extend(_parse_sections(content, rel))
        structure.labels.extend(_parse_labels(content, rel))
        structure.citations.extend(_parse_citations(content, rel))
        structure.inputs.extend(_parse_inputs(content, rel))

    return structure


def find_section(
    structure: DocumentStructure,
    title: str | None = None,
    label: str | None = None,
) -> tuple[str, int, int] | None:
    """Resolve a section anchor to ``(file, line_start, line_end)``.

    Match order:
        1. exact label match (when *label* is given)
        2. exact title match (case-sensitive)
        3. case-insensitive title match

    The end line is the line just before the next section in the same
    file (or end-of-file).  Returns None if no section matches.
    """
    sections = structure.sections
    if not sections:
        return None

    target: Section | None = None
    if label:
        for s in sections:
            if s.label == label:
                target = s
                break
    if target is None and title is not None:
        for s in sections:
            if s.title == title:
                target = s
                break
        if target is None:
            lc = title.lower()
            for s in sections:
                if s.title.lower() == lc:
                    target = s
                    break
    if target is None:
        return None

    # Determine end line: next section in the same file, or EOF
    same_file_after = [s for s in sections if s.file == target.file and s.line > target.line]
    if same_file_after:
        end_line = min(s.line for s in same_file_after) - 1
    else:
        end_line = -1  # caller should treat as "to EOF"
    return (target.file, target.line, end_line)

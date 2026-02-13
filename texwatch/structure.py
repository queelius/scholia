"""LaTeX document structure parsing.

Parses document structure from .tex files: sections, TODOs, \\input/\\include
tree, and word count (via texcount).
"""

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """A section-level heading in a LaTeX document.

    Attributes:
        level: Sectioning command ("chapter", "section", "subsection", "subsubsection").
        title: Section title text (from the mandatory {} argument).
        file: Source file path (relative to watch_dir).
        line: Line number where the heading appears (1-indexed).
    """

    level: str
    title: str
    file: str
    line: int


@dataclass
class TodoItem:
    """A TODO/FIXME/NOTE/XXX annotation found in a LaTeX file.

    Matches both comment-style (% TODO: ...) and command-style (\\todo{...}).

    Attributes:
        text: The annotation text content.
        file: Source file path (relative to watch_dir).
        line: Line number (1-indexed).
        tag: Annotation type ("TODO", "FIXME", "NOTE", or "XXX").
    """

    text: str
    file: str
    line: int
    tag: str


@dataclass
class InputFile:
    """An \\input or \\include reference in a LaTeX file.

    Attributes:
        path: The included file path as written in the source (e.g., "chapters/intro").
        file: Parent file containing the \\input command.
        line: Line number of the \\input command (1-indexed).
    """

    path: str
    file: str
    line: int


@dataclass
class SectionStats:
    """Per-section statistics.

    Attributes:
        section_title: Title of the section.
        section_file: File containing the section heading.
        section_line: Line of the section heading (1-indexed).
        start_line: First line of section content (inclusive).
        end_line: Last line of section content (inclusive).
        word_count: Approximate word count in the section range.
        citation_count: Number of citation commands in the section.
        environment_counts: Count of each environment type (e.g. {"equation": 3}).
        todo_count: Number of TODOs in the section.
        figure_count: Number of figure/figure* environments.
        table_count: Number of table/table* environments.
    """

    section_title: str
    section_file: str
    section_line: int
    start_line: int
    end_line: int
    word_count: int = 0
    citation_count: int = 0
    environment_counts: dict[str, int] = field(default_factory=dict)
    todo_count: int = 0
    figure_count: int = 0
    table_count: int = 0


@dataclass
class StructureSummary:
    """Document-level summary statistics.

    Attributes:
        total_figures: Total figure/figure* count.
        total_tables: Total table/table* count.
        total_equations: Total equation-like environment count.
        total_citations: Total citation command count.
        total_todos: Total TODO/FIXME/NOTE/XXX count.
    """

    total_figures: int = 0
    total_tables: int = 0
    total_equations: int = 0
    total_citations: int = 0
    total_todos: int = 0


@dataclass
class DocumentStructure:
    """Aggregated structure of a LaTeX project.

    Contains all sections, TODOs, input references, word count,
    per-section statistics, and a document summary.

    Attributes:
        sections: All section headings found in the project.
        todos: All TODO/FIXME/NOTE/XXX annotations.
        inputs: All \\input/\\include references.
        word_count: Total word count from texcount, or None if unavailable.
        section_stats: Per-section statistics.
        summary: Document-level summary counts.
    """

    sections: list[Section] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
    inputs: list[InputFile] = field(default_factory=list)
    word_count: int | None = None
    section_stats: list[SectionStats] = field(default_factory=list)
    summary: StructureSummary = field(default_factory=StructureSummary)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches section command prefix: \chapter, \section*, \subsection[short], etc.
# Group 1: level (chapter|section|subsection|subsubsection)
# Title is extracted separately via _extract_braced for nested-brace safety.
_SECTION_PREFIX_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)"
    r"\*?"
    r"(?:\[[^\]]*\])?"  # optional [short title]
)

# Matches % TODO: ..., % FIXME ..., % NOTE: ..., % XXX: ... (in comments)
_COMMENT_TODO_RE = re.compile(
    r"%\s*(TODO|FIXME|NOTE|XXX)[:\s]\s*(.*)",
)

# Matches \todo{...} (e.g. todonotes package)
_CMD_TODO_RE = re.compile(
    r"\\todo(?:\[[^\]]*\])?\{([^}]+)\}",
)

# Matches \input{...} and \include{...}
_INPUT_RE = re.compile(
    r"\\(input|include)\{([^}]+)\}",
)

# Regex for counting citations in a line range
_STATS_CITE_RE = re.compile(
    r"\\(cite[pt]?|citeauthor|citeyear|nocite)(?:\[[^\]]*\])*\{([^}]+)\}"
)

# Regex for counting environments in a line range
_STATS_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")

# Heuristic word regex: sequences of alphabetic chars not starting with \
_WORD_RE = re.compile(r"(?<!\\)\b[a-zA-Z]{2,}\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_preceding_backslashes(text: str, pos: int) -> int:
    """Count consecutive backslashes immediately before *pos*."""
    count = 0
    j = pos - 1
    while j >= 0 and text[j] == "\\":
        count += 1
        j -= 1
    return count


def _extract_braced(text: str, pos: int) -> tuple[str, int] | None:
    """Extract content within matched braces starting at *pos*.

    Returns ``(content, end_pos)`` where *content* excludes the outer braces
    and *end_pos* is the index after the closing ``}``.  Returns ``None`` if
    ``text[pos]`` is not ``{`` or braces are unmatched.

    Escaped braces (``\\{`` and ``\\}``) are treated as literal characters
    and do not affect depth counting.
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
    backslashes (``\\%`` = linebreak + comment, ``\\\\%`` = escaped percent).
    """
    i = 0
    while i < len(line):
        if line[i] == "%" and _count_preceding_backslashes(line, i) % 2 == 0:
            return line[:i]
        i += 1
    return line


def _find_tex_files(watch_dir: Path) -> list[Path]:
    """Recursively find all .tex files under *watch_dir*."""
    return sorted(watch_dir.rglob("*.tex"))


def _relative(path: Path, watch_dir: Path) -> str:
    """Return *path* relative to *watch_dir* as a string."""
    try:
        return str(path.relative_to(watch_dir))
    except ValueError:
        return str(path)


def _read_files(
    paths: list[Path], watch_dir: Path, label: str = "read",
) -> list[tuple[str, str]]:
    """Read files and return (content, relative_path) pairs.

    Silently skips files that cannot be read.
    """
    results: list[tuple[str, str]] = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("%s: failed to read %s", label, path)
            continue
        results.append((content, _relative(path, watch_dir)))
    return results


def _parse_sections(content: str, rel_path: str) -> list[Section]:
    """Extract section headings from file content."""
    sections: list[Section] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.lstrip()
        if stripped.startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _SECTION_PREFIX_RE.finditer(line):
            pos = m.end()
            while pos < len(line) and line[pos] in " \t":
                pos += 1
            result = _extract_braced(line, pos)
            if result:
                sections.append(
                    Section(
                        level=m.group(1),
                        title=result[0].strip(),
                        file=rel_path,
                        line=line_no,
                    )
                )
    return sections


def _parse_todos(content: str, rel_path: str) -> list[TodoItem]:
    """Extract TODO/FIXME/NOTE/XXX items from file content."""
    todos: list[TodoItem] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        # Comment-style TODOs
        m = _COMMENT_TODO_RE.search(line)
        if m:
            todos.append(
                TodoItem(
                    text=m.group(2).strip(),
                    file=rel_path,
                    line=line_no,
                    tag=m.group(1),
                )
            )
        # \todo{...} command
        for m in _CMD_TODO_RE.finditer(line):
            todos.append(
                TodoItem(
                    text=m.group(1).strip(),
                    file=rel_path,
                    line=line_no,
                    tag="TODO",
                )
            )
    return todos


def _parse_inputs(content: str, rel_path: str) -> list[InputFile]:
    """Extract \\input/\\include references from file content."""
    inputs: list[InputFile] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.lstrip()
        if stripped.startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _INPUT_RE.finditer(line):
            raw_path = m.group(2).strip()
            inputs.append(
                InputFile(
                    path=raw_path,
                    file=rel_path,
                    line=line_no,
                )
            )
    return inputs


def _get_word_count(main_file: Path) -> int | None:
    """Run ``texcount`` on *main_file* and return total word count.

    Returns ``None`` if texcount is not installed or fails.
    """
    try:
        result = subprocess.run(
            ["texcount", "-total", "-brief", str(main_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.debug("texcount returned non-zero: %s", result.stderr.strip())
            return None

        # texcount -total -brief output looks like:
        #   "Words in text: 1234\n" or just "1234+56+7 (1 file)\n"
        # We try to extract the first integer.
        output = result.stdout.strip()
        m = re.search(r"(\d+)", output)
        if m:
            count = int(m.group(1))
            logger.debug("texcount: %d words in %s", count, main_file.name)
            return count

        logger.debug("texcount: could not parse output: %r", output)
        return None

    except FileNotFoundError:
        logger.debug("texcount: not installed")
        return None
    except subprocess.TimeoutExpired:
        logger.debug("texcount: timed out")
        return None
    except Exception:
        logger.debug("texcount: unexpected error", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Section stats computation
# ---------------------------------------------------------------------------

_EQUATION_ENVS = {
    "equation", "equation*", "align", "align*",
    "gather", "gather*", "multline", "multline*",
}


def _accumulate_line_stats(line: str, summary: StructureSummary) -> None:
    """Accumulate citation, figure, table, and equation counts from a single line."""
    for m in _STATS_CITE_RE.finditer(line):
        summary.total_citations += len(m.group(2).split(","))
    for m in _STATS_BEGIN_RE.finditer(line):
        env = m.group(1)
        if env in ("figure", "figure*"):
            summary.total_figures += 1
        elif env in ("table", "table*"):
            summary.total_tables += 1
        elif env in _EQUATION_ENVS:
            summary.total_equations += 1


def _compute_section_stats(
    sections: list[Section],
    todos: list[TodoItem],
    file_contents: dict[str, str],
) -> tuple[list[SectionStats], StructureSummary]:
    """Compute per-section statistics and document summary.

    Args:
        sections: All sections found by parsing.
        todos: All TODO items found by parsing.
        file_contents: Mapping of rel_path -> file content string.

    Returns:
        Tuple of (section_stats list, summary).
    """
    stats_list: list[SectionStats] = []
    summary = StructureSummary(total_todos=len(todos))

    if not sections:
        # Still compute summary from all files
        for content in file_contents.values():
            for raw_line in content.splitlines():
                if raw_line.lstrip().startswith("%"):
                    continue
                _accumulate_line_stats(_strip_comment(raw_line), summary)
        return stats_list, summary

    for idx, sec in enumerate(sections):
        content = file_contents.get(sec.file, "")
        total_lines = len(content.splitlines()) if content else 0

        # Determine end_line: next section in same file, or EOF
        end_line = total_lines
        for next_idx in range(idx + 1, len(sections)):
            if sections[next_idx].file == sec.file:
                end_line = sections[next_idx].line - 1
                break

        start_line = min(sec.line, total_lines)

        # Extract lines for this section
        lines = content.splitlines()[start_line - 1:end_line] if content else []

        # Single pass: count words, citations, and environments
        word_count = 0
        citation_count = 0
        env_counts: dict[str, int] = {}
        for raw_line in lines:
            if raw_line.lstrip().startswith("%"):
                continue
            line = _strip_comment(raw_line)
            word_count += len(_WORD_RE.findall(line))
            for m in _STATS_CITE_RE.finditer(line):
                citation_count += len(m.group(2).split(","))
            for m in _STATS_BEGIN_RE.finditer(line):
                env = m.group(1)
                env_counts[env] = env_counts.get(env, 0) + 1

        todo_count = sum(
            1 for t in todos
            if t.file == sec.file and start_line <= t.line <= end_line
        )

        figure_count = env_counts.get("figure", 0) + env_counts.get("figure*", 0)
        table_count = env_counts.get("table", 0) + env_counts.get("table*", 0)

        stats_list.append(SectionStats(
            section_title=sec.title,
            section_file=sec.file,
            section_line=sec.line,
            start_line=start_line,
            end_line=end_line,
            word_count=word_count,
            citation_count=citation_count,
            environment_counts=env_counts,
            todo_count=todo_count,
            figure_count=figure_count,
            table_count=table_count,
        ))

        # Accumulate summary
        summary.total_citations += citation_count
        summary.total_figures += figure_count
        summary.total_tables += table_count
        for env, count in env_counts.items():
            if env in _EQUATION_ENVS:
                summary.total_equations += count

    return stats_list, summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_structure(main_file: Path, watch_dir: Path) -> DocumentStructure:
    """Parse LaTeX document structure from all .tex files in *watch_dir*.

    Args:
        main_file: Path to the main .tex file (used for texcount).
        watch_dir: Root directory to scan for .tex files.

    Returns:
        A :class:`DocumentStructure` with sections, TODOs, inputs, and
        word count.
    """
    sections: list[Section] = []
    todos: list[TodoItem] = []
    inputs: list[InputFile] = []
    file_contents: dict[str, str] = {}

    tex_files = _find_tex_files(watch_dir)
    logger.debug("structure: found %d .tex files in %s", len(tex_files), watch_dir)

    for content, rel in _read_files(tex_files, watch_dir, "structure"):
        file_contents[rel] = content
        sections.extend(_parse_sections(content, rel))
        todos.extend(_parse_todos(content, rel))
        inputs.extend(_parse_inputs(content, rel))

    word_count = _get_word_count(main_file)

    # Compute per-section stats
    section_stats, summary = _compute_section_stats(sections, todos, file_contents)

    structure = DocumentStructure(
        sections=sections,
        todos=todos,
        inputs=inputs,
        word_count=word_count,
        section_stats=section_stats,
        summary=summary,
    )

    logger.debug(
        "structure: %d sections, %d todos, %d inputs, word_count=%s",
        len(sections),
        len(todos),
        len(inputs),
        word_count,
    )

    return structure

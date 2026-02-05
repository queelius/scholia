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
class DocumentStructure:
    """Aggregated structure of a LaTeX project.

    Contains all sections, TODOs, input references, and word count
    extracted by parsing .tex files in the project.

    Attributes:
        sections: All section headings found in the project.
        todos: All TODO/FIXME/NOTE/XXX annotations.
        inputs: All \\input/\\include references.
        word_count: Total word count from texcount, or None if unavailable.
    """

    sections: list[Section] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
    inputs: list[InputFile] = field(default_factory=list)
    word_count: int | None = None


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches \chapter{...}, \section*{...}, \subsection[short]{long}, etc.
# Group 1: level (chapter|section|subsection|subsubsection)
# Group 2: optional star (*)
# Group 3: title (from the mandatory {…} argument)
_SECTION_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)"
    r"(\*?)"
    r"(?:\[[^\]]*\])?"  # optional [short title]
    r"\{([^}]+)\}",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_tex_files(watch_dir: Path) -> list[Path]:
    """Recursively find all .tex files under *watch_dir*."""
    return sorted(watch_dir.rglob("*.tex"))


def _relative(path: Path, watch_dir: Path) -> str:
    """Return *path* relative to *watch_dir* as a string."""
    try:
        return str(path.relative_to(watch_dir))
    except ValueError:
        return str(path)


def _parse_sections(content: str, rel_path: str) -> list[Section]:
    """Extract section headings from file content."""
    sections: list[Section] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        for m in _SECTION_RE.finditer(line):
            sections.append(
                Section(
                    level=m.group(1),
                    title=m.group(3).strip(),
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
    for line_no, line in enumerate(content.splitlines(), start=1):
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

    tex_files = _find_tex_files(watch_dir)
    logger.debug("structure: found %d .tex files in %s", len(tex_files), watch_dir)

    for tex_path in tex_files:
        try:
            content = tex_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("structure: failed to read %s", tex_path)
            continue

        rel = _relative(tex_path, watch_dir)
        sections.extend(_parse_sections(content, rel))
        todos.extend(_parse_todos(content, rel))
        inputs.extend(_parse_inputs(content, rel))

    word_count = _get_word_count(main_file)

    structure = DocumentStructure(
        sections=sections,
        todos=todos,
        inputs=inputs,
        word_count=word_count,
    )

    logger.debug(
        "structure: %d sections, %d todos, %d inputs, word_count=%s",
        len(sections),
        len(todos),
        len(inputs),
        word_count,
    )

    return structure

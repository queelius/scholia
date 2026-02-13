"""LaTeX document metadata extraction (digest).

Parses the main .tex file for document class, packages, title, author,
date, abstract, and custom command definitions.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .structure import _count_preceding_backslashes, _extract_braced

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Command:
    """A custom command or math operator definition.

    Attributes:
        command_type: Definition command ("newcommand", "renewcommand", "DeclareMathOperator").
        name: The command name (e.g. "\\\\myvec").
        definition: The replacement text (e.g. "\\\\mathbf{#1}").
        args: Number of arguments, or None if unspecified.
    """

    command_type: str
    name: str
    definition: str
    args: int | None = None


@dataclass
class Digest:
    """Aggregated document metadata.

    Attributes:
        documentclass: Document class name (e.g. "article", "book").
        class_options: Options passed to documentclass (e.g. ["12pt", "a4paper"]).
        title: Document title, or None.
        author: Author string, or None.
        date: Date string, or None.
        abstract: Abstract text, or None.
        packages: List of package dicts with "name" and "options" keys.
        commands: List of custom command definitions.
    """

    documentclass: str | None = None
    class_options: list[str] = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    date: str | None = None
    abstract: str | None = None
    packages: list[dict[str, str]] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_DOCUMENTCLASS_RE = re.compile(
    r"\\documentclass(?:\[([^\]]+)\])?\{([^}]+)\}"
)

_USEPACKAGE_RE = re.compile(
    r"\\usepackage(?:\[([^\]]*)\])?\{([^}]+)\}"
)

_DECLAREMATHOP_RE = re.compile(
    r"\\DeclareMathOperator\*?\{([^}]+)\}\{([^}]+)\}"
)

_ABSTRACT_RE = re.compile(
    r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL
)

# Patterns for commands whose argument may contain nested braces.
# We find the command prefix then use _extract_braced for the value.
_COMMAND_PREFIX_RE = re.compile(
    r"\\(title|author|date|newcommand|renewcommand)\b\s*"
)

_NARGS_RE = re.compile(r"\[(\d+)\]")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _skip_whitespace(text: str, pos: int) -> int:
    """Advance *pos* past spaces, tabs, and newlines in *text*."""
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    return pos


def _brace_depth_at(text: str, pos: int) -> int:
    """Return the brace nesting depth at *pos* in *text*.

    Only unescaped braces are counted (escaped ``\\{`` are ignored).
    """
    depth = 0
    for i in range(pos):
        ch = text[i]
        if ch == "{" and _count_preceding_backslashes(text, i) % 2 == 0:
            depth += 1
        elif ch == "}" and _count_preceding_backslashes(text, i) % 2 == 0:
            depth -= 1
    return depth


def _find_command_arg(text: str, command: str) -> str | None:
    """Find ``\\command{arg}`` where *arg* may contain nested braces.

    Only matches at brace depth 0 are considered, so occurrences inside
    ``\\newcommand`` definitions are skipped.
    """
    pattern = re.compile(r"\\" + re.escape(command) + r"\s*")
    for m in pattern.finditer(text):
        if _brace_depth_at(text, m.start()) > 0:
            continue
        pos = _skip_whitespace(text, m.end())
        if pos < len(text) and text[pos] == "{":
            result = _extract_braced(text, pos)
            if result:
                return result[0].strip()
    return None


def _parse_digest_content(content: str) -> Digest:
    """Parse document metadata from file content."""
    digest = Digest()

    # Split preamble from body for most fields
    parts = content.split("\\begin{document}", 1)
    preamble = parts[0] if parts else content

    # Document class
    m = _DOCUMENTCLASS_RE.search(preamble)
    if m:
        opts_str = m.group(1)
        digest.documentclass = m.group(2).strip()
        if opts_str:
            digest.class_options = [o.strip() for o in opts_str.split(",") if o.strip()]

    # Title (can be in preamble or body) — uses brace-depth for nested braces
    digest.title = _find_command_arg(content, "title")

    # Author (can be in preamble or body)
    digest.author = _find_command_arg(content, "author")

    # Date (preamble only)
    digest.date = _find_command_arg(preamble, "date")

    # Abstract (in body)
    m = _ABSTRACT_RE.search(content)
    if m:
        digest.abstract = m.group(1).strip()

    # Packages
    for m in _USEPACKAGE_RE.finditer(preamble):
        opts = m.group(1) or ""
        # Handle comma-separated package names: \usepackage{amsmath,amssymb}
        pkg_names = [p.strip() for p in m.group(2).split(",") if p.strip()]
        for pkg_name in pkg_names:
            digest.packages.append({"name": pkg_name, "options": opts.strip()})

    # Custom commands — uses brace-depth for nested braces in definitions
    for m in _COMMAND_PREFIX_RE.finditer(preamble):
        cmd_type = m.group(1)
        if cmd_type not in ("newcommand", "renewcommand"):
            continue
        pos = _skip_whitespace(preamble, m.end())
        # Extract command name: {name}
        name_result = _extract_braced(preamble, pos)
        if not name_result:
            continue
        cmd_name, pos = name_result

        # Optional [nargs]
        nargs = None
        pos = _skip_whitespace(preamble, pos)
        nargs_m = _NARGS_RE.match(preamble, pos)
        if nargs_m:
            nargs = int(nargs_m.group(1))
            pos = nargs_m.end()

        # Extract definition: {definition}
        pos = _skip_whitespace(preamble, pos)
        def_result = _extract_braced(preamble, pos)
        if not def_result:
            continue

        digest.commands.append(Command(
            command_type=cmd_type,
            name=cmd_name.strip(),
            definition=def_result[0].strip(),
            args=nargs,
        ))

    # DeclareMathOperator
    for m in _DECLAREMATHOP_RE.finditer(preamble):
        digest.commands.append(Command(
            command_type="DeclareMathOperator",
            name=m.group(1).strip(),
            definition=m.group(2).strip(),
            args=None,
        ))

    return digest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_digest(main_file: Path, watch_dir: Path) -> Digest:
    """Parse document metadata from the main .tex file.

    Args:
        main_file: Path to the main .tex file.
        watch_dir: Root directory (unused, kept for API consistency).

    Returns:
        A :class:`Digest` with document metadata.
    """
    try:
        content = main_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("digest: failed to read %s", main_file)
        return Digest()

    digest = _parse_digest_content(content)

    logger.debug(
        "digest: class=%s, title=%s, %d packages, %d commands",
        digest.documentclass,
        digest.title,
        len(digest.packages),
        len(digest.commands),
    )

    return digest

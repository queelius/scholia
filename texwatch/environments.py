"""LaTeX environment extraction.

Parses .tex files for theorem-like, math, float, list, and algorithm
environments.  Extracts labels, captions, and optional names.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .structure import _extract_braced, _find_tex_files, _read_files, _strip_comment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Environment:
    """A LaTeX environment occurrence.

    Attributes:
        env_type: Environment name (e.g. "theorem", "equation", "figure").
        label: Label from ``\\label{}`` inside, or None.
        name: Optional name from ``\\begin{theorem}[Name]``, or None.
        caption: Caption text from ``\\caption{}`` inside, or None.
        file: Source file path relative to watch_dir.
        start_line: Line number of ``\\begin{...}`` (1-indexed).
        end_line: Line number of ``\\end{...}``, or None if unclosed.
    """

    env_type: str
    label: str | None = None
    name: str | None = None
    caption: str | None = None
    file: str = ""
    start_line: int = 0
    end_line: int | None = None


# ---------------------------------------------------------------------------
# Tracked environment types
# ---------------------------------------------------------------------------

TRACKED_ENVIRONMENTS: set[str] = {
    # Theorem-like
    "theorem", "lemma", "corollary", "proposition", "definition",
    "remark", "proof", "example", "exercise",
    # Math
    "equation", "equation*", "align", "align*", "gather", "gather*",
    "multline", "multline*",
    # Floats
    "figure", "figure*", "table", "table*",
    # Lists
    "itemize", "enumerate", "description",
    # Algorithm
    "algorithm", "algorithmic",
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# \begin{envname}  or  \begin{envname}[optional name]
_BEGIN_ENV_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}(?:\[([^\]]+)\])?")

# \end{envname}
_END_ENV_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")

# \label{...}
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")

# \caption — prefix only; content extracted via _extract_braced for nested-brace safety.
_CAPTION_PREFIX_RE = re.compile(r"\\caption(?:\[[^\]]*\])?")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_environments_in_file(
    content: str, rel_path: str,
) -> list[Environment]:
    """Extract tracked environments from a single file's content.

    Uses a stack to handle nesting.  Labels and captions are attached
    to the innermost tracked environment on the stack.
    """
    envs: list[Environment] = []
    # Stack of (Environment, is_tracked) tuples.
    # We track *all* environments for correct nesting, but only record
    # those in TRACKED_ENVIRONMENTS.
    stack: list[tuple[Environment, bool]] = []

    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        # Skip comment lines
        stripped = raw_line.lstrip()
        if stripped.startswith("%"):
            continue

        # Strip inline comments before processing
        line = _strip_comment(raw_line)

        # Collect all matches with positions for ordered processing.
        # evt_data is a Match for begin/end/label events, or a str for caption events.
        events: list[tuple[int, str, re.Match[str] | str]] = []
        for mx in _BEGIN_ENV_RE.finditer(line):
            events.append((mx.start(), "begin", mx))
        for mx in _END_ENV_RE.finditer(line):
            events.append((mx.start(), "end", mx))
        for mx in _LABEL_RE.finditer(line):
            events.append((mx.start(), "label", mx))
        for mx in _CAPTION_PREFIX_RE.finditer(line):
            # Only record if a braced argument follows; store extracted text
            pos = mx.end()
            while pos < len(line) and line[pos] in " \t":
                pos += 1
            result = _extract_braced(line, pos)
            if result is not None:
                events.append((mx.start(), "caption", result[0].strip()))

        events.sort(key=lambda x: x[0])

        for evt_pos, event_type, evt_data in events:
            if event_type == "begin":
                assert isinstance(evt_data, re.Match)
                env_name = evt_data.group(1)
                opt_name = evt_data.group(2)
                tracked = env_name in TRACKED_ENVIRONMENTS
                env = Environment(
                    env_type=env_name,
                    name=opt_name.strip() if opt_name else None,
                    file=rel_path,
                    start_line=line_no,
                )
                stack.append((env, tracked))

            elif event_type == "end":
                assert isinstance(evt_data, re.Match)
                env_name = evt_data.group(1)
                # Pop matching environment from stack
                if stack:
                    top_env, top_tracked = stack[-1]
                    if top_env.env_type == env_name:
                        stack.pop()
                        if top_tracked:
                            top_env.end_line = line_no
                            envs.append(top_env)

            elif event_type == "label":
                assert isinstance(evt_data, re.Match)
                text = evt_data.group(1).strip()
                for env, tracked in reversed(stack):
                    if tracked and env.label is None:
                        env.label = text
                        break

            elif event_type == "caption":
                # evt_data is the already-extracted caption text (str)
                assert isinstance(evt_data, str)
                for env, tracked in reversed(stack):
                    if tracked and env.caption is None:
                        env.caption = evt_data
                        break

    # Any remaining unclosed environments
    for env, tracked in stack:
        if tracked:
            envs.append(env)

    return envs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_environments(main_file: Path, watch_dir: Path) -> list[Environment]:
    """Parse LaTeX environments from all .tex files in *watch_dir*.

    Args:
        main_file: Path to the main .tex file (unused, kept for API consistency).
        watch_dir: Root directory to scan for .tex files.

    Returns:
        List of :class:`Environment` instances found across all files.
    """
    all_envs: list[Environment] = []

    tex_files = _find_tex_files(watch_dir)
    for content, rel in _read_files(tex_files, watch_dir, "environments"):
        all_envs.extend(_parse_environments_in_file(content, rel))

    logger.debug("environments: found %d tracked environments", len(all_envs))
    return all_envs

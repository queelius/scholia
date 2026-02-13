"""Section-level change tracking for LaTeX documents.

Computes diffs between file snapshots at compile boundaries to identify
which sections changed and by how much.
"""

import difflib
import logging
import re
from collections import deque
from dataclasses import dataclass

from .structure import Section

logger = logging.getLogger(__name__)

# Heuristic word regex: sequences of alphabetic chars (>= 2) not preceded by \.
_WORD_RE = re.compile(r"(?<!\\)\b[a-zA-Z]{2,}\b")

# Maximum number of diff lines to include in a snippet.
_MAX_SNIPPET_LINES = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SectionDelta:
    """Change summary for a single section between two compile snapshots.

    Attributes:
        section_title: Title of the section heading.
        section_file: Source file path (relative to watch_dir).
        section_line: Line number of the section heading (1-indexed).
        lines_added: Number of lines added in this section.
        lines_removed: Number of lines removed in this section.
        words_added: Approximate word count of added text.
        words_removed: Approximate word count of removed text.
        is_dirty: Whether the section changed at all.
        diff_snippet: Truncated unified diff for the section.
        timestamp: ISO 8601 timestamp of the compile event.
    """

    section_title: str
    section_file: str
    section_line: int
    lines_added: int
    lines_removed: int
    words_added: int
    words_removed: int
    is_dirty: bool
    diff_snippet: str
    timestamp: str


class ChangeLog:
    """Ring buffer of :class:`SectionDelta` entries across compile cycles.

    Keeps at most *maxlen* individual deltas (not batches).  Oldest entries
    are silently dropped when the buffer is full.

    Attributes:
        last_compiled_snapshots: File contents dict from the most recent
            successful compile (used as the "old" side for the next diff).
    """

    def __init__(self, maxlen: int = 50) -> None:
        self._deltas: deque[SectionDelta] = deque(maxlen=maxlen)
        self.last_compiled_snapshots: dict[str, str] = {}

    @property
    def deltas(self) -> list[SectionDelta]:
        """Return all stored deltas as a plain list."""
        return list(self._deltas)

    def record(self, deltas: list[SectionDelta]) -> None:
        """Append a batch of deltas from a compile cycle."""
        for d in deltas:
            self._deltas.append(d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section_line_range(
    sec_idx: int,
    sections: list[Section],
    file_lines: int,
) -> tuple[int, int]:
    """Return (start, end) 0-indexed line range for a section.

    *start* is the 0-indexed line of the section heading itself.
    *end* is the 0-indexed line of the next section heading in the same
    file (exclusive), or the total number of lines in the file.
    """
    sec = sections[sec_idx]
    start = sec.line - 1  # convert 1-indexed to 0-indexed
    end = file_lines
    for j in range(sec_idx + 1, len(sections)):
        if sections[j].file == sec.file:
            end = sections[j].line - 1
            break
    return start, end


def _count_words(lines: list[str]) -> int:
    """Heuristic word count for a list of lines.

    Counts sequences of >= 2 alphabetic characters that are not preceded
    by a backslash (so LaTeX commands like ``\\section`` are skipped).
    """
    return sum(len(_WORD_RE.findall(line)) for line in lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_changes(
    sections: list[Section],
    old_contents: dict[str, str],
    new_contents: dict[str, str],
    timestamp: str = "",
) -> list[SectionDelta]:
    """Compute section-level diffs between old and new file contents.

    For each section, extracts the line range from both snapshots, runs a
    unified diff, and produces a :class:`SectionDelta` summarising what
    changed.

    Args:
        sections: Parsed section headings (from :func:`structure.parse_structure`).
        old_contents: Mapping of ``rel_path -> content`` from previous compile.
        new_contents: Mapping of ``rel_path -> content`` from current compile.
        timestamp: ISO 8601 timestamp to attach to every delta.

    Returns:
        One :class:`SectionDelta` per section, in the same order as *sections*.
    """
    if not sections:
        return []

    deltas: list[SectionDelta] = []

    for idx, sec in enumerate(sections):
        old_text = old_contents.get(sec.file, "")
        new_text = new_contents.get(sec.file, "")

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        # Determine line range for this section in both snapshots.
        new_start, new_end = _section_line_range(idx, sections, len(new_lines))
        new_slice = new_lines[new_start:new_end]

        # Use the same section boundary logic for the old file, but
        # clamp to the old file's actual length so we capture content
        # that was removed (old file may be longer than new).
        _, old_end = _section_line_range(idx, sections, len(old_lines))
        old_start = min(new_start, len(old_lines))
        old_slice = old_lines[old_start:old_end]

        # Unified diff between the two slices.
        diff_lines = list(
            difflib.unified_diff(
                old_slice,
                new_slice,
                fromfile=f"a/{sec.file}",
                tofile=f"b/{sec.file}",
                lineterm="",
            )
        )

        lines_added = sum(
            1
            for line in diff_lines
            if line.startswith("+") and not line.startswith("+++")
        )
        lines_removed = sum(
            1
            for line in diff_lines
            if line.startswith("-") and not line.startswith("---")
        )

        added_words = _count_words(
            [line[1:] for line in diff_lines if line.startswith("+") and not line.startswith("+++")]
        )
        removed_words = _count_words(
            [line[1:] for line in diff_lines if line.startswith("-") and not line.startswith("---")]
        )

        is_dirty = lines_added > 0 or lines_removed > 0
        snippet = "\n".join(diff_lines[:_MAX_SNIPPET_LINES]) if diff_lines else ""

        deltas.append(
            SectionDelta(
                section_title=sec.title,
                section_file=sec.file,
                section_line=sec.line,
                lines_added=lines_added,
                lines_removed=lines_removed,
                words_added=added_words,
                words_removed=removed_words,
                is_dirty=is_dirty,
                diff_snippet=snippet,
                timestamp=timestamp,
            )
        )

    return deltas

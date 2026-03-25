"""Label parsing for LaTeX documents.

Finds all \\label{} commands and resolves their context (enclosing
section or environment) by cross-referencing with structure and
environment parsers.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .structure import _find_tex_files, _read_files, _strip_comment

_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")

_PREFIX_CONTEXT = {
    "sec": "section", "subsec": "subsection", "ch": "chapter",
    "fig": "figure", "tab": "table", "eq": "equation",
    "thm": "theorem", "lem": "lemma", "def": "definition",
    "alg": "algorithm", "lst": "listing",
}


@dataclass
class Label:
    """A \\label{} occurrence in a LaTeX document."""
    key: str
    file: str
    line: int
    context: str = ""


def parse_labels(main_file: Path, watch_dir: Path) -> list[Label]:
    """Parse all \\label{} commands from .tex files.

    main_file: Path to the main .tex file (unused, kept for API consistency).
    watch_dir: Directory to search for .tex files.
    """
    tex_files = _find_tex_files(watch_dir)
    file_contents = _read_files(tex_files, watch_dir)

    labels: list[Label] = []
    for content, rel_path in file_contents:  # _read_files returns (content, rel_path) tuples
        for i, raw_line in enumerate(content.splitlines(), 1):
            stripped = raw_line.lstrip()
            if stripped.startswith("%"):
                continue
            line_text = _strip_comment(raw_line)
            for m in _LABEL_RE.finditer(line_text):
                key = m.group(1)
                context = _context_from_prefix(key)
                labels.append(Label(key=key, file=rel_path, line=i, context=context))
    return labels


def _context_from_prefix(key: str) -> str:
    """Derive context from label prefix convention (e.g. fig:name -> figure)."""
    if ":" in key:
        prefix = key.split(":")[0]
        return _PREFIX_CONTEXT.get(prefix, "")
    return ""


def enrich_labels_with_structure(labels: list[Label], sections: list, environments: list) -> None:
    """Enrich labels with context from parsed structure and environments. Modifies in-place."""
    env_ranges: list[tuple[str, int, int, str]] = []
    for env in environments:
        end = env.end_line if env.end_line is not None else float("inf")
        env_ranges.append((env.file, env.start_line, end, env.env_type))

    for label in labels:
        if label.context:
            continue
        for env_file, start, end, env_type in env_ranges:
            if label.file == env_file and start <= label.line <= end:
                label.context = env_type
                break
        else:
            best_section = None
            for sec in sections:
                if sec.file == label.file and sec.line <= label.line:
                    if best_section is None or sec.line > best_section.line:
                        best_section = sec
            if best_section:
                label.context = f"{best_section.level}: {best_section.title}"

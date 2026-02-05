"""Project discovery for multi-project texwatch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from .config import Config


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProjectConfig:
    """Configuration for a single compilable LaTeX/Markdown paper.

    Represents one entry from .texwatch.yaml, either from the top-level
    ``main:`` key or from an entry in the ``papers:`` list.

    Attributes:
        name: Project identifier (e.g., "thesis", "neurips/paper").
            Used in URLs for multi-project mode.
        directory: Absolute path to the project directory.
        main: Main file path relative to directory.
        watch: Glob patterns for files that trigger recompilation.
        ignore: Glob patterns for files to exclude from watching.
        compiler: Compiler to use ("auto", "latexmk", "pdflatex", etc.).
    """

    name: str
    directory: Path
    main: str = "main.tex"
    watch: list[str] = field(default_factory=lambda: ["*.tex", "*.md", "*.txt"])
    ignore: list[str] = field(default_factory=list)
    compiler: str = "auto"

    def to_legacy_config(self, port: int = 8765) -> Config:
        """Convert to legacy Config for existing server/compiler code."""
        # Create a synthetic config_path so get_watch_dir() resolves correctly
        config_path = self.directory / ".texwatch.yaml"
        return Config(
            main=self.main,
            watch=list(self.watch),
            ignore=list(self.ignore),
            compiler=self.compiler,
            port=port,
            config_path=config_path,
        )


# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: dict[str, Any] = {
    "skip_dirs": [
        ".*",                                                       # hidden dirs
        "build", "_build", "out", "dist",                           # build outputs
        "node_modules", "__pycache__", ".tox", ".venv", "venv", ".eggs",  # language tooling
        "archive", "old", "deprecated",                             # stale content
        "template", "templates",                                    # template dirs
    ],
}

# Defaults for yaml parsing (used when a field is missing from .texwatch.yaml)
_YAML_DEFAULTS: dict[str, Any] = {
    "compiler": "auto",
    "watch": ["*.tex", "*.md", "*.txt"],
    "ignore": [],
}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_projects(
    root: Path,
    skip_dirs: list[str] | None = None,
) -> list[ProjectConfig]:
    """Walk a directory tree and discover projects with .texwatch.yaml.

    Only directories containing a .texwatch.yaml file are returned.

    Args:
        root: Top-level directory to scan.
        skip_dirs: Glob patterns matched against directory basenames.
            Directories matching any pattern are pruned from the walk.
            Defaults to ``_BUILTIN_DEFAULTS["skip_dirs"]``.
    """
    root = root.resolve()
    if skip_dirs is None:
        skip_dirs = list(_BUILTIN_DEFAULTS["skip_dirs"])

    found: list[ProjectConfig] = []

    for dirpath in _walk_dirs(root, skip_dirs):
        yaml_path = dirpath / ".texwatch.yaml"
        if yaml_path.exists():
            configs = _configs_from_yaml(dirpath, yaml_path, root)
            found.extend(configs)

    return found


def project_config_from_dir(
    directory: Path,
    root: Path | None = None,
) -> list[ProjectConfig]:
    """Discover projects in a single directory.

    Requires .texwatch.yaml. Returns empty list if no yaml present.

    Resolution order:
    1. .texwatch.yaml with ``papers:`` key -> N ProjectConfigs
    2. .texwatch.yaml with ``main:`` key -> 1 ProjectConfig
    3. No yaml: returns []

    Args:
        directory: Absolute path to the directory.
        root: Root of the scan tree (used for naming). If None, uses directory parent.

    Returns:
        List of ProjectConfig (may be empty if no .texwatch.yaml found).
    """
    directory = directory.resolve()
    if root is None:
        root = directory.parent

    yaml_path = directory / ".texwatch.yaml"

    if yaml_path.exists():
        return _configs_from_yaml(directory, yaml_path, root)

    return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _walk_dirs(root: Path, skip_dirs: list[str]) -> list[Path]:
    """Recursively walk directories, pruning those matching *skip_dirs* globs.

    Returns directories in sorted order (for deterministic output).
    """
    dirs: list[Path] = []

    if not root.is_dir():
        return dirs

    # Include root itself
    dirs.append(root)

    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return dirs

    for entry in entries:
        if not entry.is_dir():
            continue
        if any(fnmatch(entry.name, pat) for pat in skip_dirs):
            continue
        dirs.extend(_walk_dirs(entry, skip_dirs))

    return dirs


def _configs_from_yaml(
    directory: Path,
    yaml_path: Path,
    root: Path,
) -> list[ProjectConfig]:
    """Parse .texwatch.yaml for project configs."""
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    # Multi-paper mode: papers: key
    if "papers" in data and isinstance(data["papers"], list):
        return _parse_papers_key(directory, data, root)

    # Single-paper mode: main: key
    if "main" in data:
        name = _make_name(directory, root)
        return [ProjectConfig(
            name=name,
            directory=directory,
            main=data["main"],
            watch=data.get("watch", list(_YAML_DEFAULTS["watch"])),
            ignore=data.get("ignore", list(_YAML_DEFAULTS["ignore"])),
            compiler=data.get("compiler", _YAML_DEFAULTS["compiler"]),
        )]

    # .texwatch.yaml exists but has neither papers: nor main:
    return []


def _parse_papers_key(
    directory: Path,
    data: dict[str, Any],
    root: Path,
) -> list[ProjectConfig]:
    """Parse the ``papers:`` list from .texwatch.yaml."""
    dirname = _make_name(directory, root)
    shared_watch = data.get("watch", list(_YAML_DEFAULTS["watch"]))
    shared_ignore = data.get("ignore", list(_YAML_DEFAULTS["ignore"]))
    shared_compiler = data.get("compiler", _YAML_DEFAULTS["compiler"])

    results: list[ProjectConfig] = []
    for entry in data["papers"]:
        if not isinstance(entry, dict):
            continue
        paper_name = entry.get("name")
        if not paper_name:
            continue
        name = f"{dirname}/{paper_name}"
        results.append(ProjectConfig(
            name=name,
            directory=directory,
            main=entry.get("main", "main.tex"),
            watch=entry.get("watch", list(shared_watch)),
            ignore=entry.get("ignore", list(shared_ignore)),
            compiler=entry.get("compiler", shared_compiler),
        ))
    return results


def _configs_from_autodetect(
    directory: Path,
    root: Path,
) -> list[ProjectConfig]:
    """Auto-detect projects by scanning .tex files for \\documentclass.

    Used by ``cmd_init`` to infer project structure, not by discovery.
    """
    tex_files = sorted(directory.glob("*.tex"))
    if not tex_files:
        return []

    # Check for main.tex first
    main_tex = directory / "main.tex"
    if main_tex.exists() and _is_paper_root(main_tex):
        name = _make_name(directory, root)
        return [ProjectConfig(
            name=name,
            directory=directory,
            main="main.tex",
        )]

    # Find all .tex files with \documentclass
    doc_files = [f for f in tex_files if _is_paper_root(f)]

    if not doc_files:
        return []

    if len(doc_files) == 1:
        name = _make_name(directory, root)
        return [ProjectConfig(
            name=name,
            directory=directory,
            main=doc_files[0].name,
        )]

    # Multiple documentclass files -> one paper each
    dirname = _make_name(directory, root)
    results: list[ProjectConfig] = []
    for f in doc_files:
        stem = f.stem
        name = f"{dirname}/{stem}"
        results.append(ProjectConfig(
            name=name,
            directory=directory,
            main=f.name,
        ))
    return results


# Document classes that are fragments, not compilable root papers.
_EXCLUDED_CLASSES = frozenset({"standalone", "subfiles"})

_DOCUMENTCLASS_RE = re.compile(r"\\documentclass(?:\[.*?\])?\{(\w+)\}")


def _is_paper_root(path: Path) -> bool:
    """Check whether a .tex file is a compilable root paper.

    Returns True if the file contains ``\\documentclass`` with a class name
    that is *not* in the excluded set (standalone, subfiles).
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    if "\\documentclass" not in content:
        return False

    m = _DOCUMENTCLASS_RE.search(content)
    if m is None:
        # Has \documentclass but we can't parse the class name — treat as root
        return True

    return m.group(1) not in _EXCLUDED_CLASSES


def _make_name(directory: Path, root: Path) -> str:
    """Generate a project name from directory relative to root.

    If directory IS root, use the directory's own name.
    Otherwise, use the relative path from root.
    """
    try:
        rel = directory.relative_to(root)
        if rel == Path("."):
            return directory.name
        return str(rel).replace("\\", "/")
    except ValueError:
        return directory.name

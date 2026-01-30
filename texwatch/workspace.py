"""Workspace registry and project discovery for multi-project texwatch."""

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
    """One compilable paper."""

    name: str  # e.g. "thesis", "neurips/paper"
    directory: Path  # absolute path to project dir
    main: str = "main.tex"  # main file relative to directory
    watch: list[str] = field(default_factory=lambda: ["*.tex", "*.md", "*.txt"])
    ignore: list[str] = field(default_factory=list)
    compiler: str = "auto"

    def to_legacy_config(self, port: int = 8765) -> Config:
        """Convert to legacy Config for existing server/compiler code."""
        # Create a synthetic config_path so get_watch_dir() resolves correctly
        config_path = self.directory / "texwatch.yaml"
        return Config(
            main=self.main,
            watch=list(self.watch),
            ignore=list(self.ignore),
            compiler=self.compiler,
            port=port,
            config_path=config_path,
        )


@dataclass
class WorkspaceConfig:
    """Collection of projects from ~/.texwatch/workspace.yaml."""

    port: int = 8800
    defaults: dict[str, Any] = field(default_factory=dict)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: dict[str, Any] = {
    "compiler": "auto",
    "watch": ["*.tex", "*.md", "*.txt"],
    "ignore": [],
    "skip_dirs": [
        ".*",                                                       # hidden dirs
        "build", "_build", "out", "dist",                           # build outputs
        "node_modules", "__pycache__", ".tox", ".venv", "venv", ".eggs",  # language tooling
        "archive", "old", "deprecated",                             # stale content
        "template", "templates",                                    # template dirs
    ],
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def workspace_path() -> Path:
    """Return the global workspace file path: ~/.texwatch/workspace.yaml."""
    return Path.home() / ".texwatch" / "workspace.yaml"


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


def load_workspace(path: Path | None = None) -> WorkspaceConfig | None:
    """Load workspace config from YAML file.

    Returns None if the file does not exist.
    """
    ws_path = path or workspace_path()
    if not ws_path.exists():
        return None

    with open(ws_path) as f:
        raw = yaml.safe_load(f) or {}

    port = raw.get("port", 8800)
    defaults = raw.get("defaults", {})
    projects: dict[str, ProjectConfig] = {}

    for name, entry in raw.get("projects", {}).items():
        if not isinstance(entry, dict):
            continue
        directory = Path(entry.get("path", ".")).expanduser().resolve()
        pc = _project_from_entry(name, directory, entry, defaults)
        projects[name] = pc

    return WorkspaceConfig(port=port, defaults=defaults, projects=projects)


def save_workspace(ws: WorkspaceConfig, path: Path | None = None) -> Path:
    """Save workspace config to YAML file.

    Creates parent directories if needed. Returns the path written to.
    """
    ws_path = path or workspace_path()
    ws_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {"port": ws.port}

    if ws.defaults:
        data["defaults"] = ws.defaults

    projects_data: dict[str, Any] = {}
    for name, pc in ws.projects.items():
        entry: dict[str, Any] = {"path": str(pc.directory)}
        if pc.main != "main.tex":
            entry["main"] = pc.main
        if pc.compiler != _resolve_default("compiler", ws.defaults):
            entry["compiler"] = pc.compiler
        if pc.watch != _resolve_default("watch", ws.defaults):
            entry["watch"] = pc.watch
        if pc.ignore:
            entry["ignore"] = pc.ignore
        projects_data[name] = entry

    data["projects"] = projects_data

    with open(ws_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return ws_path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_projects(
    root: Path,
    skip_dirs: list[str] | None = None,
) -> list[ProjectConfig]:
    """Walk a directory tree and discover compilable papers.

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
        configs = project_config_from_dir(dirpath, root)
        found.extend(configs)

    return found


def project_config_from_dir(
    directory: Path,
    root: Path | None = None,
) -> list[ProjectConfig]:
    """Discover projects in a single directory.

    Resolution order:
    1. texwatch.yaml with ``papers:`` key -> N ProjectConfigs
    2. texwatch.yaml with ``main:`` key -> 1 ProjectConfig
    3. No yaml: auto-detect from .tex files with \\documentclass

    Args:
        directory: Absolute path to the directory.
        root: Root of the scan tree (used for naming). If None, uses directory parent.

    Returns:
        List of ProjectConfig (may be empty if nothing detected).
    """
    directory = directory.resolve()
    if root is None:
        root = directory.parent

    yaml_path = directory / "texwatch.yaml"

    if yaml_path.exists():
        return _configs_from_yaml(directory, yaml_path, root)

    return _configs_from_autodetect(directory, root)


def merge_discovered(
    ws: WorkspaceConfig,
    found: list[ProjectConfig],
) -> WorkspaceConfig:
    """Merge newly discovered projects into workspace.

    - Adds new projects that don't already exist (by name).
    - Does NOT remove or overwrite existing entries (preserves user edits).
    """
    for pc in found:
        if pc.name not in ws.projects:
            ws.projects[pc.name] = pc
    return ws


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_default(key: str, workspace_defaults: dict[str, Any]) -> Any:
    """Resolve a default value: workspace defaults -> built-in defaults."""
    if key in workspace_defaults:
        return workspace_defaults[key]
    return _BUILTIN_DEFAULTS.get(key)


def _project_from_entry(
    name: str,
    directory: Path,
    entry: dict[str, Any],
    workspace_defaults: dict[str, Any],
) -> ProjectConfig:
    """Build a ProjectConfig from a workspace.yaml entry."""
    return ProjectConfig(
        name=name,
        directory=directory,
        main=entry.get("main", "main.tex"),
        watch=entry.get("watch", _resolve_default("watch", workspace_defaults)),
        ignore=entry.get("ignore", _resolve_default("ignore", workspace_defaults)),
        compiler=entry.get("compiler", _resolve_default("compiler", workspace_defaults)),
    )


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
    """Parse texwatch.yaml for project configs."""
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
            watch=data.get("watch", list(_BUILTIN_DEFAULTS["watch"])),
            ignore=data.get("ignore", list(_BUILTIN_DEFAULTS["ignore"])),
            compiler=data.get("compiler", _BUILTIN_DEFAULTS["compiler"]),
        )]

    # YAML exists but has neither papers: nor main: — try auto-detect
    return _configs_from_autodetect(directory, root)


def _parse_papers_key(
    directory: Path,
    data: dict[str, Any],
    root: Path,
) -> list[ProjectConfig]:
    """Parse the ``papers:`` list from texwatch.yaml."""
    dirname = _make_name(directory, root)
    shared_watch = data.get("watch", list(_BUILTIN_DEFAULTS["watch"]))
    shared_ignore = data.get("ignore", list(_BUILTIN_DEFAULTS["ignore"]))
    shared_compiler = data.get("compiler", _BUILTIN_DEFAULTS["compiler"])

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
    """Auto-detect projects by scanning .tex files for \\documentclass."""
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

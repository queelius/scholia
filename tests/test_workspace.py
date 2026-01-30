"""Tests for workspace module — discovery, YAML I/O, merge, papers: parsing."""

from pathlib import Path

import pytest
import yaml

from texwatch.workspace import (
    ProjectConfig,
    WorkspaceConfig,
    _is_paper_root,
    discover_projects,
    load_workspace,
    merge_discovered,
    project_config_from_dir,
    save_workspace,
    workspace_path,
)


# ---------------------------------------------------------------------------
# ProjectConfig dataclass
# ---------------------------------------------------------------------------


class TestProjectConfig:
    """Tests for ProjectConfig dataclass."""

    def test_defaults(self):
        """Test ProjectConfig defaults."""
        pc = ProjectConfig(name="test", directory=Path("/tmp/test"))
        assert pc.main == "main.tex"
        assert pc.compiler == "auto"
        assert "*.tex" in pc.watch
        assert pc.ignore == []

    def test_to_legacy_config(self):
        """Test conversion to legacy Config."""
        pc = ProjectConfig(
            name="thesis",
            directory=Path("/home/user/thesis"),
            main="paper.tex",
            compiler="xelatex",
        )
        cfg = pc.to_legacy_config(port=9999)
        assert cfg.main == "paper.tex"
        assert cfg.compiler == "xelatex"
        assert cfg.port == 9999
        assert cfg.config_path == Path("/home/user/thesis/texwatch.yaml")

    def test_to_legacy_config_default_port(self):
        """Test to_legacy_config with default port."""
        pc = ProjectConfig(name="test", directory=Path("/tmp/test"))
        cfg = pc.to_legacy_config()
        assert cfg.port == 8765


# ---------------------------------------------------------------------------
# WorkspaceConfig dataclass
# ---------------------------------------------------------------------------


class TestWorkspaceConfig:
    """Tests for WorkspaceConfig dataclass."""

    def test_defaults(self):
        """Test WorkspaceConfig defaults."""
        ws = WorkspaceConfig()
        assert ws.port == 8800
        assert ws.defaults == {}
        assert ws.projects == {}


# ---------------------------------------------------------------------------
# workspace_path()
# ---------------------------------------------------------------------------


class TestWorkspacePath:
    """Tests for workspace_path."""

    def test_returns_home_based_path(self):
        """Test workspace_path returns ~/.texwatch/workspace.yaml."""
        result = workspace_path()
        assert result.name == "workspace.yaml"
        assert result.parent.name == ".texwatch"


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


class TestYamlIO:
    """Tests for load_workspace and save_workspace."""

    def test_load_nonexistent(self, tmp_path):
        """Test load_workspace returns None for nonexistent file."""
        result = load_workspace(tmp_path / "missing.yaml")
        assert result is None

    def test_save_and_load_roundtrip(self, tmp_path):
        """Test saving and loading preserves data."""
        ws = WorkspaceConfig(
            port=9000,
            defaults={"compiler": "xelatex"},
            projects={
                "thesis": ProjectConfig(
                    name="thesis",
                    directory=tmp_path / "thesis",
                    main="paper.tex",
                    compiler="xelatex",
                ),
                "notes": ProjectConfig(
                    name="notes",
                    directory=tmp_path / "notes",
                    main="main.tex",
                ),
            },
        )

        ws_path = tmp_path / "ws.yaml"
        save_workspace(ws, ws_path)
        assert ws_path.exists()

        loaded = load_workspace(ws_path)
        assert loaded is not None
        assert loaded.port == 9000
        assert "thesis" in loaded.projects
        assert "notes" in loaded.projects
        assert loaded.projects["thesis"].main == "paper.tex"
        assert loaded.projects["thesis"].compiler == "xelatex"

    def test_save_creates_parent_dirs(self, tmp_path):
        """Test save_workspace creates parent directories."""
        ws = WorkspaceConfig()
        ws_path = tmp_path / "deep" / "nested" / "workspace.yaml"
        save_workspace(ws, ws_path)
        assert ws_path.exists()

    def test_load_minimal_yaml(self, tmp_path):
        """Test loading a minimal workspace yaml."""
        ws_path = tmp_path / "ws.yaml"
        ws_path.write_text(yaml.dump({
            "port": 7777,
            "projects": {
                "demo": {"path": str(tmp_path)},
            },
        }))
        ws = load_workspace(ws_path)
        assert ws is not None
        assert ws.port == 7777
        assert "demo" in ws.projects
        assert ws.projects["demo"].main == "main.tex"  # default

    def test_load_with_defaults_section(self, tmp_path):
        """Test that workspace defaults are applied to projects."""
        ws_path = tmp_path / "ws.yaml"
        ws_path.write_text(yaml.dump({
            "defaults": {"compiler": "lualatex"},
            "projects": {
                "paper": {"path": str(tmp_path)},
            },
        }))
        ws = load_workspace(ws_path)
        assert ws.projects["paper"].compiler == "lualatex"

    def test_load_project_overrides_defaults(self, tmp_path):
        """Test that project-level fields override workspace defaults."""
        ws_path = tmp_path / "ws.yaml"
        ws_path.write_text(yaml.dump({
            "defaults": {"compiler": "lualatex"},
            "projects": {
                "paper": {"path": str(tmp_path), "compiler": "xelatex"},
            },
        }))
        ws = load_workspace(ws_path)
        assert ws.projects["paper"].compiler == "xelatex"

    def test_save_omits_default_values(self, tmp_path):
        """Test that save omits fields matching defaults."""
        ws = WorkspaceConfig(
            projects={
                "test": ProjectConfig(
                    name="test",
                    directory=tmp_path,
                    main="main.tex",  # default
                    compiler="auto",  # default
                ),
            },
        )
        ws_path = tmp_path / "ws.yaml"
        save_workspace(ws, ws_path)

        with open(ws_path) as f:
            raw = yaml.safe_load(f)

        # main=main.tex should be omitted (it's the default)
        assert "main" not in raw["projects"]["test"]

    def test_load_empty_yaml(self, tmp_path):
        """Test loading an empty yaml file."""
        ws_path = tmp_path / "ws.yaml"
        ws_path.write_text("")
        ws = load_workspace(ws_path)
        assert ws is not None
        assert ws.port == 8800
        assert ws.projects == {}

    def test_load_skips_non_dict_entries(self, tmp_path):
        """Test that non-dict project entries are skipped."""
        ws_path = tmp_path / "ws.yaml"
        ws_path.write_text(yaml.dump({
            "projects": {
                "good": {"path": str(tmp_path)},
                "bad": "not a dict",
            },
        }))
        ws = load_workspace(ws_path)
        assert "good" in ws.projects
        assert "bad" not in ws.projects


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Tests for discover_projects and project_config_from_dir."""

    def test_single_main_tex(self, tmp_path):
        """Test auto-detect with main.tex."""
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
        )
        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "main.tex"

    def test_single_documentclass_file(self, tmp_path):
        """Test auto-detect with one .tex file containing documentclass."""
        (tmp_path / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
        )
        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "paper.tex"

    def test_multiple_documentclass_files(self, tmp_path):
        """Test auto-detect with multiple .tex files containing documentclass."""
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "supplement.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "helper.tex").write_text("% No documentclass here\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 2
        names = {c.main for c in configs}
        assert "paper.tex" in names
        assert "supplement.tex" in names

    def test_no_documentclass(self, tmp_path):
        """Test auto-detect with .tex files but no documentclass."""
        (tmp_path / "section.tex").write_text("\\section{Hello}\n")
        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 0

    def test_no_tex_files(self, tmp_path):
        """Test auto-detect with no .tex files."""
        (tmp_path / "readme.md").write_text("# Hello\n")
        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 0

    def test_main_tex_priority(self, tmp_path):
        """Test that main.tex takes priority over other documentclass files."""
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "main.tex"

    def test_texwatch_yaml_main(self, tmp_path):
        """Test detection from texwatch.yaml with main: key."""
        (tmp_path / "texwatch.yaml").write_text("main: thesis.tex\ncompiler: xelatex\n")
        (tmp_path / "thesis.tex").write_text("\\documentclass{article}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "thesis.tex"
        assert configs[0].compiler == "xelatex"

    def test_texwatch_yaml_papers_key(self, tmp_path):
        """Test detection from texwatch.yaml with papers: key."""
        yaml_content = {
            "papers": [
                {"name": "paper", "main": "paper.tex"},
                {"name": "supplement", "main": "supplement.tex", "compiler": "pdflatex"},
            ],
            "watch": ["*.tex", "*.bib"],
        }
        (tmp_path / "texwatch.yaml").write_text(yaml.dump(yaml_content))

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 2

        by_main = {c.main: c for c in configs}
        assert "paper.tex" in by_main
        assert "supplement.tex" in by_main
        assert by_main["supplement.tex"].compiler == "pdflatex"
        # Shared watch patterns
        assert by_main["paper.tex"].watch == ["*.tex", "*.bib"]

    def test_texwatch_yaml_papers_names_include_dirname(self, tmp_path):
        """Test that papers: names are prefixed with directory name."""
        subdir = tmp_path / "neurips"
        subdir.mkdir()
        yaml_content = {
            "papers": [
                {"name": "paper", "main": "paper.tex"},
                {"name": "supplement", "main": "supplement.tex"},
            ],
        }
        (subdir / "texwatch.yaml").write_text(yaml.dump(yaml_content))

        configs = project_config_from_dir(subdir, root=tmp_path)
        names = {c.name for c in configs}
        assert "neurips/paper" in names
        assert "neurips/supplement" in names

    def test_discover_walks_tree(self, tmp_path):
        """Test discover_projects walks directory tree."""
        # Create structure:
        # tmp_path/
        #   thesis/main.tex
        #   paper/paper.tex
        thesis = tmp_path / "thesis"
        thesis.mkdir()
        (thesis / "main.tex").write_text("\\documentclass{article}\n")

        paper = tmp_path / "paper"
        paper.mkdir()
        (paper / "paper.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        names = {c.name for c in configs}
        assert "thesis" in names
        assert "paper" in names

    def test_discover_skips_hidden_dirs(self, tmp_path):
        """Test discover_projects skips hidden directories."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any(c.name == ".hidden" for c in configs)

    def test_discover_skips_build_dirs(self, tmp_path):
        """Test discover_projects skips build output directories."""
        build = tmp_path / "build"
        build.mkdir()
        (build / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert len(configs) == 0

    def test_discover_nested_dirs(self, tmp_path):
        """Test discovery with nested directory structure."""
        # papers/
        #   2024/
        #     icml/main.tex
        nested = tmp_path / "2024" / "icml"
        nested.mkdir(parents=True)
        (nested / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert len(configs) == 1
        assert "icml" in configs[0].name or "2024/icml" in configs[0].name

    def test_texwatch_yaml_no_main_no_papers_falls_through(self, tmp_path):
        """Test texwatch.yaml with neither main: nor papers: falls to auto-detect."""
        (tmp_path / "texwatch.yaml").write_text("compiler: xelatex\n")
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "paper.tex"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


class TestMerge:
    """Tests for merge_discovered."""

    def test_merge_adds_new(self):
        """Test merge adds new projects."""
        ws = WorkspaceConfig(projects={
            "existing": ProjectConfig(name="existing", directory=Path("/tmp/a")),
        })
        found = [
            ProjectConfig(name="new1", directory=Path("/tmp/b")),
            ProjectConfig(name="new2", directory=Path("/tmp/c")),
        ]
        result = merge_discovered(ws, found)
        assert "existing" in result.projects
        assert "new1" in result.projects
        assert "new2" in result.projects

    def test_merge_does_not_overwrite(self):
        """Test merge preserves existing projects."""
        ws = WorkspaceConfig(projects={
            "keep": ProjectConfig(
                name="keep",
                directory=Path("/tmp/keep"),
                main="custom.tex",
                compiler="xelatex",
            ),
        })
        found = [
            ProjectConfig(
                name="keep",
                directory=Path("/tmp/keep"),
                main="detected.tex",  # different main
            ),
        ]
        result = merge_discovered(ws, found)
        assert result.projects["keep"].main == "custom.tex"  # preserved
        assert result.projects["keep"].compiler == "xelatex"  # preserved

    def test_merge_empty_workspace(self):
        """Test merge into empty workspace."""
        ws = WorkspaceConfig()
        found = [
            ProjectConfig(name="project", directory=Path("/tmp/p")),
        ]
        result = merge_discovered(ws, found)
        assert "project" in result.projects

    def test_merge_empty_found(self):
        """Test merge with no discovered projects."""
        ws = WorkspaceConfig(projects={
            "existing": ProjectConfig(name="existing", directory=Path("/tmp/a")),
        })
        result = merge_discovered(ws, [])
        assert len(result.projects) == 1


# ---------------------------------------------------------------------------
# _is_paper_root — documentclass filtering
# ---------------------------------------------------------------------------


class TestIsPaperRoot:
    """Tests for _is_paper_root documentclass filtering."""

    def test_article_is_root(self, tmp_path):
        """Test that \\documentclass{article} is detected as a root paper."""
        f = tmp_path / "paper.tex"
        f.write_text("\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n")
        assert _is_paper_root(f) is True

    def test_report_is_root(self, tmp_path):
        """Test that \\documentclass{report} is detected as a root paper."""
        f = tmp_path / "paper.tex"
        f.write_text("\\documentclass{report}\n\\begin{document}\n\\end{document}\n")
        assert _is_paper_root(f) is True

    def test_book_is_root(self, tmp_path):
        """Test that \\documentclass{book} is detected as a root paper."""
        f = tmp_path / "paper.tex"
        f.write_text("\\documentclass{book}\n\\begin{document}\n\\end{document}\n")
        assert _is_paper_root(f) is True

    def test_standalone_not_root(self, tmp_path):
        """Test that \\documentclass{standalone} is NOT detected as a root paper."""
        f = tmp_path / "figure.tex"
        f.write_text("\\documentclass{standalone}\n\\begin{document}\n\\tikzpicture\n\\end{document}\n")
        assert _is_paper_root(f) is False

    def test_subfiles_not_root(self, tmp_path):
        """Test that \\documentclass{subfiles} is NOT detected as a root paper."""
        f = tmp_path / "chapter.tex"
        f.write_text("\\documentclass[main.tex]{subfiles}\n\\begin{document}\n\\end{document}\n")
        assert _is_paper_root(f) is False

    def test_standalone_with_options(self, tmp_path):
        """Test standalone with optional arguments is still excluded."""
        f = tmp_path / "fig.tex"
        f.write_text("\\documentclass[border=5pt]{standalone}\n")
        assert _is_paper_root(f) is False

    def test_article_with_options(self, tmp_path):
        """Test article with optional arguments is still detected."""
        f = tmp_path / "paper.tex"
        f.write_text("\\documentclass[12pt,a4paper]{article}\n")
        assert _is_paper_root(f) is True

    def test_no_documentclass(self, tmp_path):
        """Test file without \\documentclass is not a root."""
        f = tmp_path / "section.tex"
        f.write_text("\\section{Introduction}\nHello world.\n")
        assert _is_paper_root(f) is False

    def test_nonexistent_file(self, tmp_path):
        """Test nonexistent file returns False."""
        assert _is_paper_root(tmp_path / "missing.tex") is False


# ---------------------------------------------------------------------------
# Configurable skip_dirs
# ---------------------------------------------------------------------------


class TestSkipDirs:
    """Tests for configurable skip_dirs in discover_projects."""

    def test_default_skips_archive(self, tmp_path):
        """Test that default skip_dirs skips archive/ directories."""
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any("archive" in c.name for c in configs)

    def test_default_skips_old(self, tmp_path):
        """Test that default skip_dirs skips old/ directories."""
        old = tmp_path / "old"
        old.mkdir()
        (old / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any("old" in c.name for c in configs)

    def test_default_skips_deprecated(self, tmp_path):
        """Test that default skip_dirs skips deprecated/ directories."""
        dep = tmp_path / "deprecated"
        dep.mkdir()
        (dep / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any("deprecated" in c.name for c in configs)

    def test_custom_skip_dirs(self, tmp_path):
        """Test that custom skip_dirs overrides defaults."""
        # Create a directory that would normally be kept
        drafts = tmp_path / "drafts"
        drafts.mkdir()
        (drafts / "main.tex").write_text("\\documentclass{article}\n")

        # Also create a directory that default would skip
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "main.tex").write_text("\\documentclass{article}\n")

        # Custom skip_dirs: skip "drafts" but NOT "archive"
        configs = discover_projects(tmp_path, skip_dirs=["drafts"])
        names = {c.name for c in configs}
        assert "archive" in names  # archive is NOT skipped with custom list
        assert "drafts" not in names  # drafts IS skipped

    def test_empty_skip_dirs_skips_nothing(self, tmp_path):
        """Test that empty skip_dirs list skips no directories."""
        build = tmp_path / "build"
        build.mkdir()
        (build / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path, skip_dirs=[])
        assert any("build" in c.name for c in configs)

    def test_glob_pattern_matching(self, tmp_path):
        """Test that skip_dirs uses glob matching (e.g., '.*' matches hidden dirs)."""
        hidden = tmp_path / ".secret"
        hidden.mkdir()
        (hidden / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path, skip_dirs=[".*"])
        assert not any(".secret" in c.name for c in configs)

    def test_autodetect_skips_standalone(self, tmp_path):
        """Test auto-detection skips standalone .tex files."""
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "figure.tex").write_text("\\documentclass{standalone}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "paper.tex"

    def test_autodetect_skips_subfiles(self, tmp_path):
        """Test auto-detection skips subfiles .tex files."""
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "chapter1.tex").write_text("\\documentclass[main.tex]{subfiles}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "main.tex"

    def test_autodetect_only_standalone_returns_empty(self, tmp_path):
        """Test directory with only standalone files returns no projects."""
        (tmp_path / "fig1.tex").write_text("\\documentclass{standalone}\n")
        (tmp_path / "fig2.tex").write_text("\\documentclass{standalone}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 0

    def test_skip_dirs_from_workspace_defaults(self, tmp_path):
        """Test that skip_dirs loaded from workspace defaults works."""
        # Create workspace yaml with custom skip_dirs
        ws_path = tmp_path / "ws.yaml"
        ws_path.write_text(yaml.dump({
            "defaults": {"skip_dirs": ["wip"]},
            "projects": {},
        }))
        ws = load_workspace(ws_path)
        assert ws.defaults["skip_dirs"] == ["wip"]

        # Create scan directory
        scan_root = tmp_path / "papers"
        scan_root.mkdir()
        wip = scan_root / "wip"
        wip.mkdir()
        (wip / "main.tex").write_text("\\documentclass{article}\n")
        real = scan_root / "real"
        real.mkdir()
        (real / "main.tex").write_text("\\documentclass{article}\n")

        # Use workspace skip_dirs
        configs = discover_projects(scan_root, skip_dirs=ws.defaults["skip_dirs"])
        names = {c.name for c in configs}
        assert "real" in names
        assert "wip" not in names

"""Tests for workspace module — discovery, YAML parsing, papers: key."""

from pathlib import Path

import pytest
import yaml

from texwatch.workspace import (
    ProjectConfig,
    _configs_from_autodetect,
    _is_paper_root,
    discover_projects,
    project_config_from_dir,
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
        assert cfg.config_path == Path("/home/user/thesis/.texwatch.yaml")

    def test_to_legacy_config_default_port(self):
        """Test to_legacy_config with default port."""
        pc = ProjectConfig(name="test", directory=Path("/tmp/test"))
        cfg = pc.to_legacy_config()
        assert cfg.port == 8765


# ---------------------------------------------------------------------------
# Discovery — requires .texwatch.yaml
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Tests for discover_projects and project_config_from_dir."""

    def test_yaml_with_main(self, tmp_path):
        """Test detection from .texwatch.yaml with main: key."""
        (tmp_path / ".texwatch.yaml").write_text("main: thesis.tex\ncompiler: xelatex\n")
        (tmp_path / "thesis.tex").write_text("\\documentclass{article}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 1
        assert configs[0].main == "thesis.tex"
        assert configs[0].compiler == "xelatex"

    def test_yaml_papers_key(self, tmp_path):
        """Test detection from .texwatch.yaml with papers: key."""
        yaml_content = {
            "papers": [
                {"name": "paper", "main": "paper.tex"},
                {"name": "supplement", "main": "supplement.tex", "compiler": "pdflatex"},
            ],
            "watch": ["*.tex", "*.bib"],
        }
        (tmp_path / ".texwatch.yaml").write_text(yaml.dump(yaml_content))

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 2

        by_main = {c.main: c for c in configs}
        assert "paper.tex" in by_main
        assert "supplement.tex" in by_main
        assert by_main["supplement.tex"].compiler == "pdflatex"
        # Shared watch patterns
        assert by_main["paper.tex"].watch == ["*.tex", "*.bib"]

    def test_yaml_papers_names_include_dirname(self, tmp_path):
        """Test that papers: names are prefixed with directory name."""
        subdir = tmp_path / "neurips"
        subdir.mkdir()
        yaml_content = {
            "papers": [
                {"name": "paper", "main": "paper.tex"},
                {"name": "supplement", "main": "supplement.tex"},
            ],
        }
        (subdir / ".texwatch.yaml").write_text(yaml.dump(yaml_content))

        configs = project_config_from_dir(subdir, root=tmp_path)
        names = {c.name for c in configs}
        assert "neurips/paper" in names
        assert "neurips/supplement" in names

    def test_no_yaml_returns_empty(self, tmp_path):
        """Test that directory without .texwatch.yaml returns empty list."""
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 0

    def test_yaml_no_main_no_papers_returns_empty(self, tmp_path):
        """Test .texwatch.yaml with neither main: nor papers: returns empty."""
        (tmp_path / ".texwatch.yaml").write_text("compiler: xelatex\n")
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")

        configs = project_config_from_dir(tmp_path)
        assert len(configs) == 0

    def test_discover_walks_tree(self, tmp_path):
        """Test discover_projects walks directory tree."""
        # Create structure with .texwatch.yaml in subdirs
        thesis = tmp_path / "thesis"
        thesis.mkdir()
        (thesis / ".texwatch.yaml").write_text("main: main.tex\n")
        (thesis / "main.tex").write_text("\\documentclass{article}\n")

        paper = tmp_path / "paper"
        paper.mkdir()
        (paper / ".texwatch.yaml").write_text("main: paper.tex\n")
        (paper / "paper.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        names = {c.name for c in configs}
        assert "thesis" in names
        assert "paper" in names

    def test_discover_ignores_dirs_without_yaml(self, tmp_path):
        """Test discover_projects ignores dirs that only have .tex files."""
        # Dir with .tex but no yaml
        bare = tmp_path / "bare_project"
        bare.mkdir()
        (bare / "main.tex").write_text("\\documentclass{article}\n")

        # Dir with yaml
        configured = tmp_path / "configured"
        configured.mkdir()
        (configured / ".texwatch.yaml").write_text("main: main.tex\n")
        (configured / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        names = {c.name for c in configs}
        assert "configured" in names
        assert "bare_project" not in names

    def test_discover_skips_hidden_dirs(self, tmp_path):
        """Test discover_projects skips hidden directories."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / ".texwatch.yaml").write_text("main: main.tex\n")
        (hidden / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any(c.name == ".hidden" for c in configs)

    def test_discover_skips_build_dirs(self, tmp_path):
        """Test discover_projects skips build output directories."""
        build = tmp_path / "build"
        build.mkdir()
        (build / ".texwatch.yaml").write_text("main: main.tex\n")
        (build / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert len(configs) == 0

    def test_discover_nested_dirs(self, tmp_path):
        """Test discovery with nested directory structure."""
        nested = tmp_path / "2024" / "icml"
        nested.mkdir(parents=True)
        (nested / ".texwatch.yaml").write_text("main: main.tex\n")
        (nested / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert len(configs) == 1
        assert "icml" in configs[0].name or "2024/icml" in configs[0].name


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
        (archive / ".texwatch.yaml").write_text("main: main.tex\n")
        (archive / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any("archive" in c.name for c in configs)

    def test_default_skips_old(self, tmp_path):
        """Test that default skip_dirs skips old/ directories."""
        old = tmp_path / "old"
        old.mkdir()
        (old / ".texwatch.yaml").write_text("main: main.tex\n")
        (old / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any("old" in c.name for c in configs)

    def test_default_skips_deprecated(self, tmp_path):
        """Test that default skip_dirs skips deprecated/ directories."""
        dep = tmp_path / "deprecated"
        dep.mkdir()
        (dep / ".texwatch.yaml").write_text("main: main.tex\n")
        (dep / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path)
        assert not any("deprecated" in c.name for c in configs)

    def test_custom_skip_dirs(self, tmp_path):
        """Test that custom skip_dirs overrides defaults."""
        # Create a directory that would normally be kept
        drafts = tmp_path / "drafts"
        drafts.mkdir()
        (drafts / ".texwatch.yaml").write_text("main: main.tex\n")
        (drafts / "main.tex").write_text("\\documentclass{article}\n")

        # Also create a directory that default would skip
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / ".texwatch.yaml").write_text("main: main.tex\n")
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
        (build / ".texwatch.yaml").write_text("main: main.tex\n")
        (build / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path, skip_dirs=[])
        assert any("build" in c.name for c in configs)

    def test_glob_pattern_matching(self, tmp_path):
        """Test that skip_dirs uses glob matching (e.g., '.*' matches hidden dirs)."""
        hidden = tmp_path / ".secret"
        hidden.mkdir()
        (hidden / ".texwatch.yaml").write_text("main: main.tex\n")
        (hidden / "main.tex").write_text("\\documentclass{article}\n")

        configs = discover_projects(tmp_path, skip_dirs=[".*"])
        assert not any(".secret" in c.name for c in configs)


# ---------------------------------------------------------------------------
# Auto-detect (used by cmd_init, tested directly)
# ---------------------------------------------------------------------------


class TestAutoDetect:
    """Tests for _configs_from_autodetect — used by init, not by discovery."""

    def test_single_main_tex(self, tmp_path):
        """Test auto-detect with main.tex."""
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
        )
        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 1
        assert configs[0].main == "main.tex"

    def test_single_documentclass_file(self, tmp_path):
        """Test auto-detect with one .tex file containing documentclass."""
        (tmp_path / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
        )
        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 1
        assert configs[0].main == "paper.tex"

    def test_multiple_documentclass_files(self, tmp_path):
        """Test auto-detect with multiple .tex files containing documentclass."""
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "supplement.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "helper.tex").write_text("% No documentclass here\n")

        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 2
        names = {c.main for c in configs}
        assert "paper.tex" in names
        assert "supplement.tex" in names

    def test_no_documentclass(self, tmp_path):
        """Test auto-detect with .tex files but no documentclass."""
        (tmp_path / "section.tex").write_text("\\section{Hello}\n")
        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 0

    def test_no_tex_files(self, tmp_path):
        """Test auto-detect with no .tex files."""
        (tmp_path / "readme.md").write_text("# Hello\n")
        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 0

    def test_main_tex_priority(self, tmp_path):
        """Test that main.tex takes priority over other documentclass files."""
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")

        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 1
        assert configs[0].main == "main.tex"

    def test_skips_standalone(self, tmp_path):
        """Test auto-detection skips standalone .tex files."""
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "figure.tex").write_text("\\documentclass{standalone}\n")

        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 1
        assert configs[0].main == "paper.tex"

    def test_skips_subfiles(self, tmp_path):
        """Test auto-detection skips subfiles .tex files."""
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "chapter1.tex").write_text("\\documentclass[main.tex]{subfiles}\n")

        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 1
        assert configs[0].main == "main.tex"

    def test_only_standalone_returns_empty(self, tmp_path):
        """Test directory with only standalone files returns no projects."""
        (tmp_path / "fig1.tex").write_text("\\documentclass{standalone}\n")
        (tmp_path / "fig2.tex").write_text("\\documentclass{standalone}\n")

        configs = _configs_from_autodetect(tmp_path, tmp_path.parent)
        assert len(configs) == 0

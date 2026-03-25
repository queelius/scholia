"""Tests for label parsing."""

import pytest
from pathlib import Path

from texwatch.labels import Label, parse_labels


@pytest.fixture
def tex_dir(tmp_path):
    """Create a directory with .tex files containing labels."""
    main = tmp_path / "main.tex"
    main.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "\\label{sec:intro}\n"
        "Some text.\n"
        "\\begin{equation}\n"
        "E = mc^2\n"
        "\\label{eq:einstein}\n"
        "\\end{equation}\n"
        "\\begin{figure}\n"
        "\\caption{A figure}\n"
        "\\label{fig:example}\n"
        "\\end{figure}\n"
        "\\end{document}\n"
    )
    return tmp_path


class TestParseLabels:
    def test_finds_all_labels(self, tex_dir):
        labels = parse_labels(tex_dir / "main.tex", tex_dir)
        keys = {l.key for l in labels}
        assert keys == {"sec:intro", "eq:einstein", "fig:example"}

    def test_label_has_file_and_line(self, tex_dir):
        labels = parse_labels(tex_dir / "main.tex", tex_dir)
        intro = next(l for l in labels if l.key == "sec:intro")
        assert intro.file == "main.tex"
        assert intro.line == 4

    def test_label_context_from_prefix(self, tex_dir):
        """Labels with standard prefixes get context from prefix."""
        labels = parse_labels(tex_dir / "main.tex", tex_dir)
        eq = next(l for l in labels if l.key == "eq:einstein")
        assert eq.context

    def test_no_labels_returns_empty(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n")
        labels = parse_labels(main, tmp_path)
        assert labels == []

    def test_duplicate_labels_all_returned(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("\\label{foo}\n\\label{foo}\n")
        labels = parse_labels(main, tmp_path)
        assert len(labels) == 2

    def test_ignores_commented_labels(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("% \\label{commented}\n\\label{real}\n")
        labels = parse_labels(main, tmp_path)
        assert len(labels) == 1
        assert labels[0].key == "real"

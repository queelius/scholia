"""Tests for environment extraction module."""

from pathlib import Path

import pytest

from texwatch.environments import (
    Environment,
    TRACKED_ENVIRONMENTS,
    _parse_environments_in_file,
    parse_environments,
)


# ---------------------------------------------------------------------------
# Single-file environment parsing
# ---------------------------------------------------------------------------


class TestParseEnvironments:
    """Tests for _parse_environments_in_file helper."""

    def test_simple_theorem(self):
        content = (
            "\\begin{theorem}\n"
            "Statement here.\n"
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].env_type == "theorem"
        assert result[0].start_line == 1
        assert result[0].end_line == 3
        assert result[0].file == "main.tex"

    def test_theorem_with_name(self):
        content = (
            "\\begin{theorem}[Fermat's Last]\n"
            "No solution.\n"
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert result[0].name == "Fermat's Last"

    def test_theorem_with_label(self):
        content = (
            "\\begin{theorem}\n"
            "\\label{thm:main}\n"
            "Statement.\n"
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert result[0].label == "thm:main"

    def test_equation(self):
        content = (
            "\\begin{equation}\n"
            "\\label{eq:euler}\n"
            "e^{i\\pi} + 1 = 0\n"
            "\\end{equation}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].env_type == "equation"
        assert result[0].label == "eq:euler"

    def test_equation_star(self):
        content = (
            "\\begin{equation*}\n"
            "x = 1\n"
            "\\end{equation*}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].env_type == "equation*"

    def test_figure_with_caption_and_label(self):
        content = (
            "\\begin{figure}\n"
            "\\centering\n"
            "\\includegraphics{fig1}\n"
            "\\caption{A nice figure}\n"
            "\\label{fig:one}\n"
            "\\end{figure}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].env_type == "figure"
        assert result[0].caption == "A nice figure"
        assert result[0].label == "fig:one"

    def test_table_with_caption(self):
        content = (
            "\\begin{table}\n"
            "\\caption[short]{Results summary}\n"
            "\\begin{tabular}{cc}\n"
            "a & b \\\\\n"
            "\\end{tabular}\n"
            "\\end{table}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        # tabular is not tracked, only table
        assert len(result) == 1
        assert result[0].env_type == "table"
        assert result[0].caption == "Results summary"

    def test_nested_equation_in_theorem(self):
        content = (
            "\\begin{theorem}\n"
            "\\label{thm:nested}\n"
            "\\begin{equation}\n"
            "\\label{eq:inside}\n"
            "x = 1\n"
            "\\end{equation}\n"
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 2
        # Equation comes first (closed first)
        eq = [e for e in result if e.env_type == "equation"][0]
        thm = [e for e in result if e.env_type == "theorem"][0]
        assert eq.label == "eq:inside"
        assert thm.label == "thm:nested"

    def test_multiple_environments(self):
        content = (
            "\\begin{lemma}\n"
            "\\end{lemma}\n"
            "\\begin{proof}\n"
            "\\end{proof}\n"
            "\\begin{definition}\n"
            "\\end{definition}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 3
        types = [e.env_type for e in result]
        assert "lemma" in types
        assert "proof" in types
        assert "definition" in types

    def test_skips_untracked_environments(self):
        content = (
            "\\begin{center}\n"
            "Centered text.\n"
            "\\end{center}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 0

    def test_caption_with_nested_braces(self):
        """Caption containing LaTeX commands with nested braces."""
        content = (
            "\\begin{figure}\n"
            "\\caption{The \\textbf{important} result}\n"
            "\\end{figure}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].caption == "The \\textbf{important} result"

    def test_caption_deeply_nested(self):
        """Caption with multiple nesting levels."""
        content = (
            "\\begin{figure}\n"
            "\\caption{Plot of $f(x) = \\frac{1}{x}$ vs $g(x)$}\n"
            "\\end{figure}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert "\\frac{1}{x}" in result[0].caption

    def test_inline_comment_hides_begin(self):
        """\\begin after inline % should be ignored."""
        content = (
            "text here % \\begin{theorem}\n"
            "more text\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert result == []

    def test_inline_comment_hides_label(self):
        """\\label after inline % should be ignored."""
        content = (
            "\\begin{theorem}\n"
            "Statement. % \\label{thm:fake}\n"
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].label is None

    def test_skips_comment_lines(self):
        content = (
            "% \\begin{theorem}\n"
            "% \\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert result == []

    def test_unclosed_environment(self):
        content = (
            "\\begin{theorem}\n"
            "Statement without end.\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].end_line is None

    def test_align_environment(self):
        content = (
            "\\begin{align}\n"
            "\\label{eq:system}\n"
            "x &= 1 \\\\\n"
            "y &= 2\n"
            "\\end{align}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].env_type == "align"
        assert result[0].label == "eq:system"

    def test_itemize_enumerate(self):
        content = (
            "\\begin{itemize}\n"
            "\\item One\n"
            "\\item Two\n"
            "\\end{itemize}\n"
            "\\begin{enumerate}\n"
            "\\item First\n"
            "\\end{enumerate}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 2
        types = {e.env_type for e in result}
        assert types == {"itemize", "enumerate"}

    def test_end_with_empty_stack(self):
        r"""An \end{} with no matching \begin{} should not crash.

        Covers branches at lines 144->126 (stack empty) and the
        'end' event type with no stack to pop from.
        """
        content = (
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert result == []

    def test_end_mismatched_env_name(self):
        r"""An \end{} with a different env name than stack top is ignored.

        Covers the branch at line 146->126: top_env.env_type != env_name.
        """
        content = (
            "\\begin{theorem}\n"
            "\\end{lemma}\n"
            "\\end{theorem}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        # The mismatched \end{lemma} is skipped; theorem still closes
        assert len(result) == 1
        assert result[0].env_type == "theorem"
        assert result[0].end_line == 3

    def test_label_inside_untracked_only(self):
        r"""A \label inside only untracked environments should not attach.

        Covers the branch at lines 155->126 / 156->155: the loop over
        reversed(stack) finds no tracked environment with label=None.
        """
        content = (
            "\\begin{center}\n"
            "\\label{lbl:center}\n"
            "\\end{center}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        # center is not tracked, so nothing is returned
        assert result == []

    def test_caption_inside_untracked_only(self):
        r"""A \caption inside only untracked environments should not attach.

        Covers the branch at lines 170->126 / 171->170: the loop over
        reversed(stack) finds no tracked environment with caption=None.
        """
        content = (
            "\\begin{minipage}{0.5\\textwidth}\n"
            "\\caption{Orphan caption}\n"
            "\\end{minipage}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        # minipage is not tracked
        assert result == []

    def test_caption_without_braced_arg(self):
        r"""A \caption not followed by braced arg should be ignored.

        Covers the branch at line 121->116: _extract_braced returns None
        so the caption event is never appended.
        """
        content = (
            "\\begin{figure}\n"
            "\\caption\n"
            "\\end{figure}\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert len(result) == 1
        assert result[0].caption is None

    def test_unclosed_untracked_env_not_returned(self):
        r"""Unclosed untracked environments should not appear in results.

        Covers the branch at line 177->176: an unclosed environment where
        tracked is False should not be appended.
        """
        content = (
            "\\begin{center}\n"
            "Text without closing.\n"
        )
        result = _parse_environments_in_file(content, "main.tex")
        assert result == []


# ---------------------------------------------------------------------------
# Full integration: parse_environments
# ---------------------------------------------------------------------------


class TestParseEnvironmentsIntegration:
    """Integration tests for parse_environments."""

    def test_multi_file(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text(
            "\\begin{document}\n"
            "\\begin{theorem}[Main Result]\n"
            "\\label{thm:main}\n"
            "\\end{theorem}\n"
            "\\end{document}\n"
        )
        ch = tmp_path / "chapter.tex"
        ch.write_text(
            "\\begin{equation}\n"
            "\\label{eq:ch}\n"
            "x=1\n"
            "\\end{equation}\n"
            "\\begin{figure}\n"
            "\\caption{Figure caption}\n"
            "\\end{figure}\n"
        )

        result = parse_environments(main, tmp_path)
        types = {e.env_type for e in result}
        assert "theorem" in types
        assert "equation" in types
        assert "figure" in types

    def test_empty_project(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("No environments here.\n")
        result = parse_environments(main, tmp_path)
        assert result == []

"""Tests for document digest (metadata) parsing module."""

from pathlib import Path

import pytest

from texwatch.digest import (
    Command,
    Digest,
    _parse_digest_content,
    parse_digest,
)


# ---------------------------------------------------------------------------
# Content-level parsing
# ---------------------------------------------------------------------------


class TestParseDigestContent:
    """Tests for _parse_digest_content helper."""

    def test_documentclass(self):
        content = "\\documentclass{article}\n"
        result = _parse_digest_content(content)
        assert result.documentclass == "article"
        assert result.class_options == []

    def test_documentclass_with_options(self):
        content = "\\documentclass[12pt, a4paper]{report}\n"
        result = _parse_digest_content(content)
        assert result.documentclass == "report"
        assert result.class_options == ["12pt", "a4paper"]

    def test_title(self):
        content = "\\title{A Great Paper}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.title == "A Great Paper"

    def test_author(self):
        content = "\\author{John Smith}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.author == "John Smith"

    def test_date(self):
        content = "\\date{2024}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.date == "2024"

    def test_abstract(self):
        content = (
            "\\begin{document}\n"
            "\\begin{abstract}\n"
            "This paper presents...\n"
            "\\end{abstract}\n"
            "\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert result.abstract is not None
        assert "This paper presents" in result.abstract

    def test_abstract_multiline(self):
        content = (
            "\\begin{document}\n"
            "\\begin{abstract}\n"
            "First line.\n"
            "Second line.\n"
            "Third line.\n"
            "\\end{abstract}\n"
            "\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert "First line" in result.abstract
        assert "Third line" in result.abstract

    def test_single_package(self):
        content = "\\usepackage{amsmath}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.packages) == 1
        assert result.packages[0]["name"] == "amsmath"
        assert result.packages[0]["options"] == ""

    def test_package_with_options(self):
        content = "\\usepackage[utf8]{inputenc}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.packages) == 1
        assert result.packages[0]["name"] == "inputenc"
        assert result.packages[0]["options"] == "utf8"

    def test_multiple_packages(self):
        content = (
            "\\usepackage{amsmath}\n"
            "\\usepackage[colorlinks]{hyperref}\n"
            "\\usepackage{graphicx}\n"
            "\\begin{document}\n\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert len(result.packages) == 3
        names = [p["name"] for p in result.packages]
        assert "amsmath" in names
        assert "hyperref" in names
        assert "graphicx" in names

    def test_comma_separated_packages(self):
        content = "\\usepackage{amsmath,amssymb,amsthm}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.packages) == 3
        names = [p["name"] for p in result.packages]
        assert names == ["amsmath", "amssymb", "amsthm"]

    def test_newcommand(self):
        content = "\\newcommand{\\myvec}[1]{\\mathbf{#1}}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].command_type == "newcommand"
        assert result.commands[0].name == "\\myvec"
        assert result.commands[0].args == 1

    def test_renewcommand(self):
        content = "\\renewcommand{\\vec}{\\mathbf}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].command_type == "renewcommand"

    def test_newcommand_no_args(self):
        content = "\\newcommand{\\R}{\\mathbb{R}}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].args is None

    def test_declaremathoperator(self):
        content = "\\DeclareMathOperator{\\argmax}{arg\\,max}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].command_type == "DeclareMathOperator"
        assert result.commands[0].name == "\\argmax"

    def test_declaremathoperator_starred(self):
        content = "\\DeclareMathOperator*{\\argmin}{arg\\,min}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].name == "\\argmin"

    def test_title_with_nested_braces(self):
        """Title containing LaTeX commands with braces."""
        content = "\\title{On the {Turing} Machine}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.title == "On the {Turing} Machine"

    def test_title_with_textbf(self):
        """Title containing \\textbf with nested braces."""
        content = "\\title{A \\textbf{Bold} Title}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.title == "A \\textbf{Bold} Title"

    def test_author_with_nested_braces(self):
        """Author with brace-protected name parts."""
        content = "\\author{John {Smith} Jr.}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.author == "John {Smith} Jr."

    def test_newcommand_with_nested_braces(self):
        """Newcommand definition body containing nested braces."""
        content = "\\newcommand{\\R}{\\mathbb{R}}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].definition == "\\mathbb{R}"

    def test_newcommand_deeply_nested(self):
        """Newcommand with multiple nesting levels."""
        content = "\\newcommand{\\foo}[1]{\\textbf{\\emph{#1}}}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert len(result.commands) == 1
        assert result.commands[0].definition == "\\textbf{\\emph{#1}}"
        assert result.commands[0].args == 1

    def test_command_arg_no_brace_after_command(self):
        r"""When \title is found but not followed by '{', it should be None.

        Covers the branch at lines 103-104: pos < len(text) but text[pos]
        is not '{', so _find_command_arg returns None.
        """
        content = "\\title some text without braces\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.title is None

    def test_newcommand_missing_name_brace(self):
        r"""A \newcommand not followed by {name} should be skipped.

        Covers the branch at line 159-160: name_result is None.
        """
        content = "\\newcommand no_braces_here\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.commands == []

    def test_newcommand_missing_definition_brace(self):
        r"""A \newcommand{name} without a {definition} should be skipped.

        Covers the branch at lines 175-177: def_result is None.
        """
        content = "\\newcommand{\\myfoo}\n\\begin{document}\n\\end{document}\n"
        result = _parse_digest_content(content)
        assert result.commands == []

    def test_empty_content(self):
        result = _parse_digest_content("")
        assert result.documentclass is None
        assert result.title is None
        assert result.packages == []
        assert result.commands == []

    def test_full_preamble(self):
        content = (
            "\\documentclass[11pt]{article}\n"
            "\\usepackage{amsmath}\n"
            "\\usepackage[utf8]{inputenc}\n"
            "\\newcommand{\\R}{\\mathbb{R}}\n"
            "\\title{My Paper}\n"
            "\\author{Jane Doe}\n"
            "\\date{2024}\n"
            "\\begin{document}\n"
            "\\begin{abstract}\n"
            "We study...\n"
            "\\end{abstract}\n"
            "\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert result.documentclass == "article"
        assert result.class_options == ["11pt"]
        assert result.title == "My Paper"
        assert result.author == "Jane Doe"
        assert result.date == "2024"
        assert "We study" in result.abstract
        assert len(result.packages) == 2
        assert len(result.commands) == 1


# ---------------------------------------------------------------------------
# Full integration: parse_digest
# ---------------------------------------------------------------------------


class TestParseDigest:
    """Integration tests for parse_digest."""

    def test_reads_main_file(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text(
            "\\documentclass{book}\n"
            "\\title{Test Book}\n"
            "\\begin{document}\n"
            "\\end{document}\n"
        )
        result = parse_digest(main, tmp_path)
        assert result.documentclass == "book"
        assert result.title == "Test Book"

    def test_nonexistent_file(self, tmp_path):
        main = tmp_path / "nonexistent.tex"
        result = parse_digest(main, tmp_path)
        assert result.documentclass is None

    def test_empty_file(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("")
        result = parse_digest(main, tmp_path)
        assert result.documentclass is None
        assert result.packages == []


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


class TestDigestRegression:
    """Regression tests for digest parsing edge cases."""

    def test_title_inside_newcommand_skipped(self):
        r"""A \title inside \newcommand should be skipped in favor of real \title."""
        content = (
            "\\newcommand{\\mytitle}{\\title{Fake}}\n"
            "\\title{Real Title}\n"
            "\\begin{document}\n\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert result.title == "Real Title"

    def test_author_inside_newcommand_skipped(self):
        r"""A \author inside \newcommand should be skipped."""
        content = (
            "\\newcommand{\\myauthor}{\\author{Fake}}\n"
            "\\author{Real Author}\n"
            "\\begin{document}\n\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert result.author == "Real Author"

    def test_title_only_inside_newcommand(self):
        r"""If \title only appears inside \newcommand, title should be None."""
        content = (
            "\\newcommand{\\mytitle}{\\title{Only Fake}}\n"
            "\\begin{document}\n\\end{document}\n"
        )
        result = _parse_digest_content(content)
        assert result.title is None

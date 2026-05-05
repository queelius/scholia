"""Tests for the slimmed structure parser (sections, labels, citations, inputs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from texwatch.structure import (
    Citation,
    DocumentStructure,
    InputFile,
    Label,
    Section,
    _extract_braced,
    _parse_citations,
    _parse_inputs,
    _parse_labels,
    _parse_sections,
    _strip_comment,
    find_section,
    parse_structure,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestExtractBraced:
    def test_simple(self):
        assert _extract_braced("{hello}", 0) == ("hello", 7)

    def test_nested(self):
        assert _extract_braced("{a {b} c}", 0) == ("a {b} c", 9)

    def test_unmatched(self):
        assert _extract_braced("{no close", 0) is None

    def test_escaped_braces(self):
        assert _extract_braced(r"{\{inner\}}", 0) == (r"\{inner\}", 11)


class TestStripComment:
    def test_full_comment(self):
        assert _strip_comment("% comment") == ""

    def test_inline_comment(self):
        assert _strip_comment("text % comment") == "text "

    def test_escaped_percent(self):
        assert _strip_comment("50\\% of data") == "50\\% of data"


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------


class TestParseSections:
    def test_simple(self):
        result = _parse_sections(r"\section{Introduction}", "main.tex")
        assert result == [Section(level="section", title="Introduction", file="main.tex", line=1)]

    def test_levels(self):
        for level in ("chapter", "section", "subsection", "subsubsection"):
            result = _parse_sections(rf"\{level}{{Title}}", "f.tex")
            assert result[0].level == level

    def test_starred(self):
        result = _parse_sections(r"\section*{Acknowledgments}", "f.tex")
        assert result[0].title == "Acknowledgments"

    def test_optional_short_title(self):
        result = _parse_sections(r"\section[Short]{Long Title}", "f.tex")
        assert result[0].title == "Long Title"

    def test_nested_braces_in_title(self):
        result = _parse_sections(r"\section{The $O(n^{2})$ Algorithm}", "f.tex")
        assert result[0].title == "The $O(n^{2})$ Algorithm"

    def test_section_picks_up_following_label(self):
        content = "\\section{Methods}\n\\label{sec:methods}\n"
        result = _parse_sections(content, "f.tex")
        assert result[0].label == "sec:methods"

    def test_section_without_label(self):
        content = "\\section{Methods}\nsome prose\n"
        result = _parse_sections(content, "f.tex")
        assert result[0].label is None

    def test_commented_section_ignored(self):
        result = _parse_sections("% \\section{Old}\n\\section{New}\n", "f.tex")
        assert len(result) == 1
        assert result[0].title == "New"

    def test_multiple_sections(self):
        content = "\\section{A}\n\\subsection{B}\n\\subsubsection{C}\n"
        result = _parse_sections(content, "f.tex")
        assert [(s.line, s.level) for s in result] == [
            (1, "section"),
            (2, "subsection"),
            (3, "subsubsection"),
        ]

    def test_no_sections(self):
        assert _parse_sections("Some prose without sections.\n", "f.tex") == []


# ---------------------------------------------------------------------------
# Label parsing
# ---------------------------------------------------------------------------


class TestParseLabels:
    def test_simple(self):
        result = _parse_labels(r"\label{eq:foo}", "f.tex")
        assert result == [Label(name="eq:foo", file="f.tex", line=1)]

    def test_multiple_per_line(self):
        result = _parse_labels(r"\label{a}\label{b}", "f.tex")
        assert {l.name for l in result} == {"a", "b"}

    def test_commented_label_ignored(self):
        assert _parse_labels(r"% \label{ignored}", "f.tex") == []


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------


class TestParseCitations:
    def test_cite(self):
        result = _parse_citations(r"See \cite{ref1}.", "f.tex")
        assert result == [Citation(key="ref1", file="f.tex", line=1)]

    def test_multiple_keys(self):
        result = _parse_citations(r"See \cite{a, b , c}.", "f.tex")
        assert [r.key for r in result] == ["a", "b", "c"]

    def test_citep_citet(self):
        result = _parse_citations(r"\citep{x} and \citet{y}.", "f.tex")
        assert {r.key for r in result} == {"x", "y"}

    def test_commented_cite_ignored(self):
        result = _parse_citations(r"% \cite{hidden}", "f.tex")
        assert result == []

    def test_inline_comment_strips_cite(self):
        result = _parse_citations(r"real \cite{a} % \cite{hidden}", "f.tex")
        assert [r.key for r in result] == ["a"]


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


class TestParseInputs:
    def test_input(self):
        assert _parse_inputs(r"\input{intro}", "main.tex") == [
            InputFile(path="intro", file="main.tex", line=1)
        ]

    def test_include(self):
        assert _parse_inputs(r"\include{appendix}", "main.tex") == [
            InputFile(path="appendix", file="main.tex", line=1)
        ]

    def test_commented_input_ignored(self):
        result = _parse_inputs("% \\input{old}\n\\input{real}\n", "main.tex")
        assert len(result) == 1
        assert result[0].path == "real"


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestParseStructure:
    def _setup_project(self, tmp_path: Path) -> Path:
        main = tmp_path / "main.tex"
        main.write_text(
            "\\documentclass{article}\n"
            "\\input{preamble}\n"
            "\\begin{document}\n"
            "\\section{Introduction}\n"
            "\\label{sec:intro}\n"
            "Some prose with \\cite{ref1, ref2}.\n"
            "\\input{chapters/methods}\n"
            "\\end{document}\n"
        )
        preamble = tmp_path / "preamble.tex"
        preamble.write_text("\\usepackage{amsmath}\n")
        chapters = tmp_path / "chapters"
        chapters.mkdir()
        (chapters / "methods.tex").write_text(
            "\\section*{Methods}\n"
            "See \\cite{ref3}.\n"
            "\\label{sec:methods}\n"
        )
        return main

    def test_parses_all_sections(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        titles = {s.title for s in ds.sections}
        assert {"Introduction", "Methods"} <= titles

    def test_parses_labels(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        names = {l.name for l in ds.labels}
        assert {"sec:intro", "sec:methods"} <= names

    def test_parses_citations(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        keys = {c.key for c in ds.citations}
        assert keys == {"ref1", "ref2", "ref3"}

    def test_parses_inputs(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        paths = {i.path for i in ds.inputs}
        assert {"preamble", "chapters/methods"} <= paths

    def test_section_picks_up_following_label(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        intro = next(s for s in ds.sections if s.title == "Introduction")
        assert intro.label == "sec:intro"

    def test_paths_are_relative(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        for s in ds.sections:
            assert not Path(s.file).is_absolute()

    def test_empty_directory(self, tmp_path):
        ds = parse_structure(tmp_path)
        assert ds == DocumentStructure()


# ---------------------------------------------------------------------------
# find_section
# ---------------------------------------------------------------------------


class TestFindSection:
    @pytest.fixture
    def structure(self) -> DocumentStructure:
        return DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
                Section(level="section", title="Methods", file="main.tex", line=50, label="sec:methods"),
                Section(level="subsection", title="Setup", file="main.tex", line=55),
                Section(level="section", title="Results", file="main.tex", line=120),
            ],
        )

    def test_match_by_title(self, structure):
        assert find_section(structure, title="Methods") == ("main.tex", 50, 54)

    def test_match_by_label(self, structure):
        assert find_section(structure, label="sec:methods") == ("main.tex", 50, 54)

    def test_match_case_insensitive_title(self, structure):
        assert find_section(structure, title="methods") == ("main.tex", 50, 54)

    def test_match_last_section_returns_eof_marker(self, structure):
        assert find_section(structure, title="Results") == ("main.tex", 120, -1)

    def test_no_match(self, structure):
        assert find_section(structure, title="Nonexistent") is None

    def test_label_takes_priority_over_title(self, structure):
        # If both are given, the label match wins
        assert find_section(structure, title="Wrong", label="sec:methods") == ("main.tex", 50, 54)

    def test_empty_structure(self):
        assert find_section(DocumentStructure(), title="x") is None

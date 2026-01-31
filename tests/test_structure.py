"""Tests for structure parsing module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from texwatch.structure import (
    DocumentStructure,
    InputFile,
    Section,
    TodoItem,
    _find_tex_files,
    _get_word_count,
    _parse_inputs,
    _parse_sections,
    _parse_todos,
    _relative,
    parse_structure,
)


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------


class TestParseSections:
    """Tests for _parse_sections helper."""

    def test_section(self):
        content = r"\section{Introduction}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].level == "section"
        assert result[0].title == "Introduction"
        assert result[0].file == "main.tex"
        assert result[0].line == 1

    def test_chapter(self):
        content = r"\chapter{Background}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].level == "chapter"
        assert result[0].title == "Background"

    def test_subsection(self):
        content = r"\subsection{Problem Statement}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].level == "subsection"
        assert result[0].title == "Problem Statement"

    def test_subsubsection(self):
        content = r"\subsubsection{Detailed Analysis}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].level == "subsubsection"
        assert result[0].title == "Detailed Analysis"

    def test_starred_section(self):
        content = r"\section*{Acknowledgments}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].level == "section"
        assert result[0].title == "Acknowledgments"

    def test_starred_chapter(self):
        content = r"\chapter*{Preface}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].level == "chapter"
        assert result[0].title == "Preface"

    def test_optional_short_title(self):
        content = r"\section[Short]{A Very Long Section Title}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].title == "A Very Long Section Title"

    def test_starred_with_optional(self):
        """Starred with optional arg (unusual but syntactically possible)."""
        content = r"\section*[toc]{No Number}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 1
        assert result[0].title == "No Number"

    def test_multiple_sections(self):
        content = (
            "\\section{First}\n"
            "Some text.\n"
            "\\subsection{Second}\n"
            "More text.\n"
            "\\subsubsection{Third}\n"
        )
        result = _parse_sections(content, "ch1.tex")
        assert len(result) == 3
        assert result[0].line == 1
        assert result[0].level == "section"
        assert result[1].line == 3
        assert result[1].level == "subsection"
        assert result[2].line == 5
        assert result[2].level == "subsubsection"

    def test_no_sections(self):
        content = "Just some plain text.\n\\begin{equation} x=1 \\end{equation}\n"
        result = _parse_sections(content, "main.tex")
        assert result == []

    def test_section_title_with_spaces(self):
        content = r"\section{  Related Work  }"
        result = _parse_sections(content, "main.tex")
        assert result[0].title == "Related Work"

    def test_two_sections_on_same_line(self):
        """Edge case: two sections on the same line."""
        content = r"\section{A} \section{B}"
        result = _parse_sections(content, "main.tex")
        assert len(result) == 2
        assert result[0].title == "A"
        assert result[1].title == "B"


# ---------------------------------------------------------------------------
# TODO parsing
# ---------------------------------------------------------------------------


class TestParseTodos:
    """Tests for _parse_todos helper."""

    def test_todo_comment(self):
        content = "% TODO: fix this later"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].tag == "TODO"
        assert result[0].text == "fix this later"
        assert result[0].file == "main.tex"
        assert result[0].line == 1

    def test_fixme_comment(self):
        content = "% FIXME needs refactoring"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].tag == "FIXME"
        assert result[0].text == "needs refactoring"

    def test_note_comment(self):
        content = "% NOTE: check with reviewer"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].tag == "NOTE"
        assert result[0].text == "check with reviewer"

    def test_xxx_comment(self):
        content = "% XXX: placeholder"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].tag == "XXX"
        assert result[0].text == "placeholder"

    def test_todo_command(self):
        content = r"\todo{Revise this paragraph}"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].tag == "TODO"
        assert result[0].text == "Revise this paragraph"

    def test_todo_command_with_option(self):
        content = r"\todo[inline]{Add more details}"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].text == "Add more details"

    def test_todo_command_with_color_option(self):
        content = r"\todo[color=red]{Important fix needed}"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].text == "Important fix needed"

    def test_multiple_todos(self):
        content = (
            "% TODO: first item\n"
            "Some text.\n"
            "% FIXME second item\n"
            r"\todo{third item}" "\n"
        )
        result = _parse_todos(content, "main.tex")
        assert len(result) == 3
        assert result[0].line == 1
        assert result[1].line == 3
        assert result[2].line == 4

    def test_no_todos(self):
        content = "% This is a regular comment\n\\begin{document}\n"
        result = _parse_todos(content, "main.tex")
        assert result == []

    def test_todo_without_colon(self):
        content = "% TODO fix alignment"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 1
        assert result[0].text == "fix alignment"

    def test_both_comment_and_command_on_same_line(self):
        """A line with both a comment TODO and \\todo command."""
        content = r"\todo{cmd todo} % TODO: comment todo"
        result = _parse_todos(content, "main.tex")
        assert len(result) == 2
        tags = {r.tag for r in result}
        assert "TODO" in tags


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


class TestParseInputs:
    """Tests for _parse_inputs helper."""

    def test_input(self):
        content = r"\input{chapters/intro}"
        result = _parse_inputs(content, "main.tex")
        assert len(result) == 1
        assert result[0].path == "chapters/intro"
        assert result[0].file == "main.tex"
        assert result[0].line == 1

    def test_include(self):
        content = r"\include{appendix}"
        result = _parse_inputs(content, "main.tex")
        assert len(result) == 1
        assert result[0].path == "appendix"

    def test_input_with_tex_extension(self):
        content = r"\input{chapters/intro.tex}"
        result = _parse_inputs(content, "main.tex")
        assert len(result) == 1
        assert result[0].path == "chapters/intro.tex"

    def test_multiple_inputs(self):
        content = (
            r"\input{preamble}" "\n"
            r"\input{chapters/intro}" "\n"
            r"\include{chapters/methods}" "\n"
            r"\input{chapters/results}" "\n"
        )
        result = _parse_inputs(content, "main.tex")
        assert len(result) == 4
        assert result[0].path == "preamble"
        assert result[1].path == "chapters/intro"
        assert result[2].path == "chapters/methods"
        assert result[3].path == "chapters/results"

    def test_no_inputs(self):
        content = "\\begin{document}\nHello.\n\\end{document}\n"
        result = _parse_inputs(content, "main.tex")
        assert result == []

    def test_input_with_spaces(self):
        content = r"\input{ chapters/intro }"
        result = _parse_inputs(content, "main.tex")
        assert len(result) == 1
        assert result[0].path == "chapters/intro"


# ---------------------------------------------------------------------------
# Word count (texcount integration)
# ---------------------------------------------------------------------------


class TestGetWordCount:
    """Tests for _get_word_count helper."""

    def test_returns_none_when_texcount_not_installed(self, tmp_path):
        """When texcount is not on PATH, return None."""
        main_file = tmp_path / "main.tex"
        main_file.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")
        with patch("texwatch.structure.subprocess.run", side_effect=FileNotFoundError):
            result = _get_word_count(main_file)
        assert result is None

    def test_returns_none_on_nonzero_exit(self, tmp_path):
        """When texcount fails, return None."""
        main_file = tmp_path / "main.tex"
        main_file.write_text("")
        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
        with patch("texwatch.structure.subprocess.run", return_value=mock_result):
            result = _get_word_count(main_file)
        assert result is None

    def test_returns_count_from_texcount_output(self, tmp_path):
        """Parse word count from texcount brief output."""
        main_file = tmp_path / "main.tex"
        main_file.write_text("")
        mock_result = type("R", (), {"returncode": 0, "stdout": "1234+56+7 (1 file)\n", "stderr": ""})()
        with patch("texwatch.structure.subprocess.run", return_value=mock_result):
            result = _get_word_count(main_file)
        assert result == 1234

    def test_returns_count_words_in_text_format(self, tmp_path):
        """Parse 'Words in text: N' output format."""
        main_file = tmp_path / "main.tex"
        main_file.write_text("")
        mock_result = type("R", (), {"returncode": 0, "stdout": "Words in text: 5678\n", "stderr": ""})()
        with patch("texwatch.structure.subprocess.run", return_value=mock_result):
            result = _get_word_count(main_file)
        assert result == 5678

    def test_returns_none_on_timeout(self, tmp_path):
        """When texcount times out, return None."""
        import subprocess

        main_file = tmp_path / "main.tex"
        main_file.write_text("")
        with patch(
            "texwatch.structure.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="texcount", timeout=30),
        ):
            result = _get_word_count(main_file)
        assert result is None

    def test_returns_none_on_unparseable_output(self, tmp_path):
        """When texcount output is empty or unparseable, return None."""
        main_file = tmp_path / "main.tex"
        main_file.write_text("")
        mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch("texwatch.structure.subprocess.run", return_value=mock_result):
            result = _get_word_count(main_file)
        assert result is None

    def test_returns_none_on_unexpected_exception(self, tmp_path):
        """When subprocess.run raises an unexpected error, return None."""
        main_file = tmp_path / "main.tex"
        main_file.write_text("")
        with patch(
            "texwatch.structure.subprocess.run",
            side_effect=RuntimeError("unexpected"),
        ):
            result = _get_word_count(main_file)
        assert result is None


# ---------------------------------------------------------------------------
# Helper: find_tex_files
# ---------------------------------------------------------------------------


class TestRelative:
    """Tests for _relative helper."""

    def test_relative_under_watch_dir(self, tmp_path):
        result = _relative(tmp_path / "chapters" / "intro.tex", tmp_path)
        assert result == "chapters/intro.tex"

    def test_relative_outside_watch_dir(self, tmp_path):
        """Path outside watch_dir falls back to str(path)."""
        other = Path("/some/other/dir/main.tex")
        result = _relative(other, tmp_path)
        assert result == "/some/other/dir/main.tex"


class TestFindTexFiles:
    """Tests for _find_tex_files helper."""

    def test_finds_tex_in_root(self, tmp_path):
        (tmp_path / "main.tex").write_text("")
        (tmp_path / "preamble.tex").write_text("")
        result = _find_tex_files(tmp_path)
        assert len(result) == 2

    def test_finds_tex_recursively(self, tmp_path):
        (tmp_path / "main.tex").write_text("")
        sub = tmp_path / "chapters"
        sub.mkdir()
        (sub / "intro.tex").write_text("")
        (sub / "methods.tex").write_text("")
        result = _find_tex_files(tmp_path)
        assert len(result) == 3

    def test_ignores_non_tex(self, tmp_path):
        (tmp_path / "main.tex").write_text("")
        (tmp_path / "figure.png").write_bytes(b"")
        (tmp_path / "notes.txt").write_text("")
        result = _find_tex_files(tmp_path)
        assert len(result) == 1

    def test_empty_directory(self, tmp_path):
        result = _find_tex_files(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Full integration: parse_structure
# ---------------------------------------------------------------------------


class TestParseStructure:
    """Integration tests for parse_structure."""

    def _setup_project(self, tmp_path):
        """Create a realistic multi-file LaTeX project in tmp_path."""
        main = tmp_path / "main.tex"
        main.write_text(
            "\\documentclass{article}\n"
            "\\input{preamble}\n"
            "\\begin{document}\n"
            "\\section{Introduction}\n"
            "% TODO: write intro\n"
            "\\input{chapters/methods}\n"
            "\\include{chapters/results}\n"
            "\\end{document}\n"
        )

        preamble = tmp_path / "preamble.tex"
        preamble.write_text(
            "\\usepackage{amsmath}\n"
            "% NOTE: add more packages as needed\n"
        )

        chapters = tmp_path / "chapters"
        chapters.mkdir()

        methods = chapters / "methods.tex"
        methods.write_text(
            "\\section{Methods}\n"
            "\\subsection{Data Collection}\n"
            "% FIXME: update data source\n"
            "\\todo{Add more details on sampling}\n"
            "\\subsubsection{Preprocessing}\n"
        )

        results = chapters / "results.tex"
        results.write_text(
            "\\section*{Results}\n"
            "\\subsection{Main Findings}\n"
            "% XXX: placeholder numbers\n"
        )

        return main

    def test_sections_found(self, tmp_path):
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)

        levels = [(s.level, s.title) for s in ds.sections]
        assert ("section", "Introduction") in levels
        assert ("section", "Methods") in levels
        assert ("subsection", "Data Collection") in levels
        assert ("subsubsection", "Preprocessing") in levels
        assert ("section", "Results") in levels  # starred
        assert ("subsection", "Main Findings") in levels

    def test_todos_found(self, tmp_path):
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)

        tags = [(t.tag, t.text) for t in ds.todos]
        assert ("TODO", "write intro") in tags
        assert ("FIXME", "update data source") in tags
        assert ("NOTE", "add more packages as needed") in tags
        assert ("XXX", "placeholder numbers") in tags
        # \todo command
        assert ("TODO", "Add more details on sampling") in tags

    def test_inputs_found(self, tmp_path):
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)

        paths = [i.path for i in ds.inputs]
        assert "preamble" in paths
        assert "chapters/methods" in paths
        assert "chapters/results" in paths

    def test_input_parent_files(self, tmp_path):
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)

        parent_map = {i.path: i.file for i in ds.inputs}
        assert parent_map["preamble"] == "main.tex"
        assert parent_map["chapters/methods"] == "main.tex"
        assert parent_map["chapters/results"] == "main.tex"

    def test_word_count_none_by_default(self, tmp_path):
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)
        assert ds.word_count is None

    def test_word_count_propagated(self, tmp_path):
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=4200):
            ds = parse_structure(main, tmp_path)
        assert ds.word_count == 4200

    def test_empty_directory(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("")
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)
        assert ds.sections == []
        assert ds.todos == []
        assert ds.inputs == []
        assert ds.word_count is None

    def test_single_file(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text(
            "\\section{Only Section}\n"
            "% TODO: only todo\n"
        )
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)
        assert len(ds.sections) == 1
        assert len(ds.todos) == 1

    def test_relative_file_paths(self, tmp_path):
        """All file paths in results should be relative to watch_dir."""
        main = self._setup_project(tmp_path)
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)

        # Sections
        for s in ds.sections:
            assert not Path(s.file).is_absolute(), f"Section file should be relative: {s.file}"

        # Todos
        for t in ds.todos:
            assert not Path(t.file).is_absolute(), f"Todo file should be relative: {t.file}"

        # Inputs (parent file)
        for i in ds.inputs:
            assert not Path(i.file).is_absolute(), f"Input parent file should be relative: {i.file}"

    def test_line_numbers_are_correct(self, tmp_path):
        """Verify 1-indexed line numbers."""
        main = tmp_path / "main.tex"
        main.write_text(
            "first line\n"           # line 1
            "\\section{Test}\n"       # line 2
            "third line\n"           # line 3
            "% TODO: something\n"    # line 4
            "\\input{other}\n"        # line 5
        )
        with patch("texwatch.structure._get_word_count", return_value=None):
            ds = parse_structure(main, tmp_path)
        assert ds.sections[0].line == 2
        assert ds.todos[0].line == 4
        assert ds.inputs[0].line == 5

    def test_unreadable_file_skipped(self, tmp_path):
        """Unreadable files should be silently skipped."""
        main = tmp_path / "main.tex"
        main.write_text("\\section{OK}\n")

        bad = tmp_path / "bad.tex"
        bad.write_text("\\section{Bad}\n")

        with (
            patch("texwatch.structure._get_word_count", return_value=None),
            patch("pathlib.Path.read_text", side_effect=[main.read_text(), OSError("denied")]),
        ):
            ds = parse_structure(main, tmp_path)

        # Should still have parsed the first file
        assert len(ds.sections) == 1
        assert ds.sections[0].title == "OK"


class TestDocumentStructureDefaults:
    """Tests for DocumentStructure dataclass defaults."""

    def test_default_fields(self):
        ds = DocumentStructure()
        assert ds.sections == []
        assert ds.todos == []
        assert ds.inputs == []
        assert ds.word_count is None

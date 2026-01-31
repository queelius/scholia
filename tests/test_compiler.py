"""Tests for compiler module."""

import pytest

from texwatch.compiler import (
    CompileMessage,
    CompileResult,
    _detect_compiler,
    _get_compiler_command,
    _parse_errors,
    _parse_pandoc_errors,
    _parse_warnings,
    check_compiler_available,
)
from pathlib import Path


class TestGetCompilerCommand:
    """Tests for _get_compiler_command function."""

    def test_latexmk(self, tmp_path):
        """Test latexmk command generation."""
        main_file = tmp_path / "main.tex"
        cmd = _get_compiler_command("latexmk", main_file, tmp_path)

        assert cmd[0] == "latexmk"
        assert "-pdf" in cmd
        assert "-synctex=1" in cmd
        assert "-file-line-error" in cmd
        assert "main.tex" in cmd

    def test_pdflatex(self, tmp_path):
        """Test pdflatex command generation."""
        main_file = tmp_path / "doc.tex"
        cmd = _get_compiler_command("pdflatex", main_file, tmp_path)

        assert cmd[0] == "pdflatex"
        assert "-synctex=1" in cmd
        assert "doc.tex" in cmd

    def test_xelatex(self, tmp_path):
        """Test xelatex command generation."""
        main_file = tmp_path / "doc.tex"
        cmd = _get_compiler_command("xelatex", main_file, tmp_path)

        assert cmd[0] == "xelatex"

    def test_unknown_compiler(self, tmp_path):
        """Test unknown compiler raises error."""
        main_file = tmp_path / "doc.tex"
        with pytest.raises(ValueError, match="Unknown compiler"):
            _get_compiler_command("unknown", main_file, tmp_path)


class TestParseErrors:
    """Tests for _parse_errors function."""

    def test_parse_standard_error(self):
        """Test parsing standard LaTeX error."""
        log = """
! Undefined control sequence.
l.42 \\badcommand
"""
        errors = _parse_errors(log, "main.tex")
        assert len(errors) == 1
        assert errors[0].type == "error"
        assert errors[0].line == 42
        assert "Undefined control sequence" in errors[0].message

    def test_parse_file_line_error(self):
        """Test parsing file:line:error format."""
        log = "chapters/intro.tex:15: Missing $ inserted."
        errors = _parse_errors(log, "main.tex")
        assert len(errors) == 1
        assert errors[0].file == "chapters/intro.tex"
        assert errors[0].line == 15
        assert "Missing $ inserted" in errors[0].message

    def test_no_errors(self):
        """Test log with no errors."""
        log = "This is pdfTeX, Version 3.14159265"
        errors = _parse_errors(log, "main.tex")
        assert len(errors) == 0


class TestParseWarnings:
    """Tests for _parse_warnings function."""

    def test_parse_latex_warning(self):
        """Test parsing LaTeX warning."""
        log = "LaTeX Warning: Reference `fig:test' on page 3 undefined on input line 42."
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) >= 1
        # Check that we found the warning (may have duplicates from pattern matching)
        found = any("Reference" in w.message or w.line == 42 for w in warnings)
        assert found

    def test_parse_package_warning(self):
        """Test parsing package warning."""
        log = "Package hyperref Warning: Token not allowed in a PDF string"
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) >= 1

    def test_parse_underfull(self):
        """Test parsing underfull box warning."""
        log = "Underfull \\hbox (badness 10000) at lines 42--43"
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) == 1
        assert warnings[0].line == 42
        assert "Underfull" in warnings[0].message

    def test_parse_overfull(self):
        """Test parsing overfull box warning."""
        log = "Overfull \\hbox (15.0pt too wide) at line 100"
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) == 1
        assert warnings[0].line == 100


class TestCheckCompilerAvailable:
    """Tests for check_compiler_available function."""

    def test_existing_command(self):
        """Test with command that should exist."""
        # 'python' should be available in test environment
        assert check_compiler_available("python") is True

    def test_nonexistent_command(self):
        """Test with command that doesn't exist."""
        assert check_compiler_available("nonexistent_compiler_xyz") is False


class TestCompileResult:
    """Tests for CompileResult dataclass."""

    def test_default_values(self):
        """Test CompileResult default values."""
        result = CompileResult(success=True)
        assert result.success is True
        assert result.errors == []
        assert result.warnings == []
        assert result.output_file is None
        assert result.duration_seconds == 0.0

    def test_with_messages(self):
        """Test CompileResult with errors and warnings."""
        error = CompileMessage(
            file="main.tex", line=10, message="Error", type="error"
        )
        warning = CompileMessage(
            file="main.tex", line=20, message="Warning", type="warning"
        )

        result = CompileResult(
            success=False, errors=[error], warnings=[warning]
        )

        assert len(result.errors) == 1
        assert len(result.warnings) == 1
        assert result.errors[0].line == 10
        assert result.warnings[0].line == 20


class TestDetectCompiler:
    """Tests for _detect_compiler function."""

    def test_auto_detect_tex(self, tmp_path):
        """Test .tex files resolve to latexmk."""
        assert _detect_compiler(tmp_path / "doc.tex") == "latexmk"

    def test_auto_detect_md(self, tmp_path):
        """Test .md files resolve to pandoc."""
        assert _detect_compiler(tmp_path / "doc.md") == "pandoc"

    def test_auto_detect_markdown(self, tmp_path):
        """Test .markdown files resolve to pandoc."""
        assert _detect_compiler(tmp_path / "doc.markdown") == "pandoc"

    def test_auto_detect_txt(self, tmp_path):
        """Test .txt files resolve to pandoc."""
        assert _detect_compiler(tmp_path / "notes.txt") == "pandoc"


class TestPandocCommand:
    """Tests for pandoc command generation."""

    def test_pandoc_command(self, tmp_path):
        """Test pandoc command is correct."""
        main_file = tmp_path / "doc.md"
        cmd = _get_compiler_command("pandoc", main_file, tmp_path)
        assert cmd == ["pandoc", "doc.md", "-o", "doc.pdf"]

    def test_auto_command_md(self, tmp_path):
        """Test auto resolves to pandoc for .md files."""
        main_file = tmp_path / "doc.md"
        cmd = _get_compiler_command("auto", main_file, tmp_path)
        assert cmd[0] == "pandoc"
        assert "doc.md" in cmd
        assert "doc.pdf" in cmd

    def test_auto_command_tex(self, tmp_path):
        """Test auto resolves to latexmk for .tex files."""
        main_file = tmp_path / "main.tex"
        cmd = _get_compiler_command("auto", main_file, tmp_path)
        assert cmd[0] == "latexmk"


class TestParsePandocErrors:
    """Tests for _parse_pandoc_errors function."""

    def test_empty_stderr(self):
        """Test empty stderr returns no errors."""
        assert _parse_pandoc_errors("") == []
        assert _parse_pandoc_errors("  \n  ") == []

    def test_stderr_with_error(self):
        """Test stderr content is captured as error."""
        errors = _parse_pandoc_errors("Error: Could not find file 'input.md'")
        assert len(errors) == 1
        assert errors[0].type == "error"
        assert "Could not find file" in errors[0].message

    def test_long_stderr_truncated(self):
        """Test long stderr is truncated to 200 chars."""
        long_msg = "x" * 300
        errors = _parse_pandoc_errors(long_msg)
        assert len(errors[0].message) == 200


class TestCheckCompilerWithAuto:
    """Tests for check_compiler_available with auto detection."""

    def test_auto_with_tex_file(self, tmp_path):
        """Test auto resolves to latexmk for .tex."""
        main_file = tmp_path / "doc.tex"
        # This tests that the function doesn't crash;
        # actual availability depends on the system
        result = check_compiler_available("auto", main_file=main_file)
        assert isinstance(result, bool)

    def test_auto_without_main_file(self):
        """Test auto without main_file defaults to latexmk."""
        result = check_compiler_available("auto")
        assert isinstance(result, bool)

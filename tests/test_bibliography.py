"""Tests for bibliography parsing module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from texwatch.bibliography import (
    BibEntry,
    Bibliography,
    Citation,
    _parse_bib_file,
    _parse_citations,
    parse_bibliography,
)


# ---------------------------------------------------------------------------
# BibTeX file parsing
# ---------------------------------------------------------------------------


class TestParseBibFile:
    """Tests for _parse_bib_file helper."""

    def test_single_article(self):
        content = (
            '@article{knuth1984,\n'
            '  author = {Donald Knuth},\n'
            '  title = {Literate Programming},\n'
            '  year = {1984},\n'
            '  journal = {The Computer Journal},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert result[0].key == "knuth1984"
        assert result[0].entry_type == "article"
        assert result[0].fields["author"] == "Donald Knuth"
        assert result[0].fields["title"] == "Literate Programming"
        assert result[0].fields["year"] == "1984"
        assert result[0].file == "refs.bib"
        assert result[0].line == 1

    def test_book_entry(self):
        content = (
            '@book{lamport1994,\n'
            '  author = {Leslie Lamport},\n'
            '  title = {LaTeX: A Document Preparation System},\n'
            '  year = {1994},\n'
            '  publisher = {Addison-Wesley},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert result[0].entry_type == "book"
        assert result[0].key == "lamport1994"

    def test_multiple_entries(self):
        content = (
            '@article{smith2020,\n'
            '  author = {John Smith},\n'
            '  title = {First Paper},\n'
            '  year = {2020},\n'
            '}\n'
            '\n'
            '@inproceedings{jones2021,\n'
            '  author = {Jane Jones},\n'
            '  title = {Second Paper},\n'
            '  year = {2021},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 2
        assert result[0].key == "smith2020"
        assert result[1].key == "jones2021"
        assert result[1].entry_type == "inproceedings"

    def test_quoted_field_values(self):
        content = (
            '@article{test2020,\n'
            '  author = "John Smith",\n'
            '  title = "A Test Paper",\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert result[0].fields["author"] == "John Smith"
        assert result[0].fields["title"] == "A Test Paper"

    def test_skips_string_preamble_comment(self):
        content = (
            '@string{jcss = "Journal of Computer and System Sciences"}\n'
            '@preamble{"\\newcommand{\\noopsort}[1]{}"}\n'
            '@comment{This is a comment.}\n'
            '@article{real2020,\n'
            '  author = {Real Author},\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert result[0].key == "real2020"

    def test_case_insensitive_entry_type(self):
        content = (
            '@Article{Upper2020,\n'
            '  Author = {Upper Case},\n'
            '  Year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert result[0].entry_type == "article"
        assert result[0].key == "Upper2020"

    def test_nested_braces_in_title(self):
        """Brace-protected words like {Knuth} must be preserved."""
        content = (
            '@article{knuth1984,\n'
            '  title = {The {\\TeX}book},\n'
            '  author = {Donald E. {Knuth}},\n'
            '  year = {1984},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert result[0].fields["title"] == "The {\\TeX}book"
        assert result[0].fields["author"] == "Donald E. {Knuth}"

    def test_deeply_nested_braces(self):
        """Multiple levels of nesting in field values."""
        content = (
            '@article{test2020,\n'
            '  title = {A {Review} of {Complex {Nested}} Methods},\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert result[0].fields["title"] == "A {Review} of {Complex {Nested}} Methods"

    def test_latex_accent_in_author(self):
        """LaTeX accent commands with braces in author names."""
        content = (
            '@article{muller2024,\n'
            '  author = {M{\\"u}ller, Hans},\n'
            '  year = {2024},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert "ller" in result[0].fields["author"]

    def test_empty_file(self):
        result = _parse_bib_file("", "refs.bib")
        assert result == []

    def test_entry_line_numbers(self):
        content = (
            '% comment\n'          # line 1
            '\n'                    # line 2
            '@article{a2020,\n'    # line 3
            '  author = {A},\n'
            '}\n'
            '\n'                    # line 6
            '@book{b2021,\n'       # line 7
            '  author = {B},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert result[0].line == 3
        assert result[1].line == 7

    def test_field_value_at_end_of_entry_text(self):
        """Field whose value is only whitespace after '=' before entry ends.

        Covers the branch at line 159-160: pos >= len(entry_text) after
        skipping whitespace past the '=' sign.
        """
        content = (
            '@article{trunc2020,\n'
            '  author = \n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert result[0].key == "trunc2020"
        # author field has no parseable value, so it should be absent
        assert "author" not in result[0].fields

    def test_field_with_unmatched_opening_brace(self):
        """Field with '{' that is never closed within the entry text.

        Covers the branch at line 162-163: _extract_braced returns None
        when braces are unmatched.
        """
        content = (
            '@article{broken2020,\n'
            '  title = {Unclosed title,\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        # title extraction may fail due to unmatched braces, but entry is still parsed
        assert result[0].key == "broken2020"

    def test_extract_braced_returns_none(self):
        """Entry where a field's braced value truly fails _extract_braced.

        The entry text is crafted so the opening brace for 'title' has no
        matching close within the entry_text slice, forcing the 163->153 branch.
        """
        # Single-line entry: the brace-depth parser includes up to the last '}'
        # but the field regex for 'title' finds '= {val' where the only closing
        # braces belong to 'year' and the entry itself.
        # We need a field that starts with '{' but _extract_braced sees no
        # balanced close. This can happen when the field text contains mismatched
        # braces within the already-extracted entry_text.
        content = (
            '@article{mismatch2020,\n'
            '  note = {has open { but no close,\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert result[0].key == "mismatch2020"
        # 'note' field should not be in fields since _extract_braced can't balance
        # (the opening '{' of note has the '{' inside it unmatched)

    def test_string_entry_matching_regex_is_skipped(self):
        """@string entries that happen to match the entry regex are skipped.

        Covers line 115: the continue statement for @string/@preamble/@comment.
        """
        content = (
            '@string{abbrev, fullname = {Full Journal Name}}\n'
            '@comment{note, this is a comment}\n'
            '@preamble{cmd, another}\n'
            '@article{real2020,\n'
            '  author = {Author},\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        # Only the @article should be returned
        assert len(result) == 1
        assert result[0].key == "real2020"

    def test_field_value_all_whitespace_to_end(self):
        """Field where everything after '=' is whitespace until end of entry text.

        Covers lines 158 and 160: the while loop consumes all remaining
        characters, pos >= len(entry_text) becomes True, and we continue.
        """
        # Construct an entry where the last field has '=' followed only by
        # whitespace until the closing brace of the entry.
        # The brace-depth counter stops at the final '}', so entry_text
        # includes up to (but not including) the last '}'.
        # Actually, entry_text includes the closing brace. But the field
        # regex matches 'note = ' and then we skip whitespace: ' \n'.
        # After that we hit '}' which is NOT whitespace, so pos < len.
        # To truly hit pos >= len(entry_text), we need the '=' at the very
        # end of the entry text.
        #
        # The entry text is extracted by brace-depth. If the entry is:
        #   @article{key, note =    }
        # Then entry_text = '@article{key, note =    }'
        # After 'note = ', pos advances through spaces to '}', which is
        # not whitespace. So we'd need the '=' truly at the end.
        #
        # A single-line entry: '@article{x, z = }'
        # entry_text = '@article{x, z = }'
        # field 'z' matched, pos after '= ' -> skip space -> hits '}' at
        # len-1, not past end.
        #
        # To make pos >= len we need the field '=' at end without closing brace
        # in same entry_text. This happens when braces never close.
        content = '@article{trunc2020, note =  \n'
        result = _parse_bib_file(content, "refs.bib")
        # The entry braces never close, so entry_text is the whole remaining text
        # After 'note = ', skipping whitespace reaches end of entry_text
        assert len(result) == 1
        assert result[0].key == "trunc2020"
        assert "note" not in result[0].fields

    def test_field_quoted_value_missing_closing_quote(self):
        """Field with opening quote but no closing quote.

        Covers the branch at line 165-167: end < 0 when closing quote
        is not found.
        """
        content = (
            '@article{noquote2020,\n'
            '  title = "No closing quote,\n'
            '  year = {2020},\n'
            '}\n'
        )
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        # title should not appear since the quoted value is malformed
        assert "title" not in result[0].fields
        # year should still be parsed
        assert result[0].fields.get("year") == "2020"


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------


class TestParseCitations:
    """Tests for _parse_citations helper."""

    def test_simple_cite(self):
        content = r"See \cite{knuth1984} for details."
        result = _parse_citations(content, "main.tex")
        assert len(result) == 1
        assert result[0].command == "cite"
        assert result[0].keys == ["knuth1984"]
        assert result[0].file == "main.tex"
        assert result[0].line == 1

    def test_cite_multiple_keys(self):
        content = r"See \cite{knuth1984, lamport1994} for details."
        result = _parse_citations(content, "main.tex")
        assert len(result) == 1
        assert result[0].keys == ["knuth1984", "lamport1994"]

    def test_citep(self):
        content = r"Results show improvements \citep{smith2020}."
        result = _parse_citations(content, "main.tex")
        assert result[0].command == "citep"

    def test_citet(self):
        content = r"\citet{jones2021} showed that..."
        result = _parse_citations(content, "main.tex")
        assert result[0].command == "citet"

    def test_citeauthor_citeyear(self):
        content = (
            r"\citeauthor{smith2020} published in \citeyear{smith2020}."
        )
        result = _parse_citations(content, "main.tex")
        assert len(result) == 2
        assert result[0].command == "citeauthor"
        assert result[1].command == "citeyear"

    def test_nocite(self):
        content = r"\nocite{hidden2020}"
        result = _parse_citations(content, "main.tex")
        assert len(result) == 1
        assert result[0].command == "nocite"

    def test_cite_with_optional_args(self):
        content = r"\cite[p.~42]{knuth1984}"
        result = _parse_citations(content, "main.tex")
        assert len(result) == 1
        assert result[0].keys == ["knuth1984"]

    def test_citep_with_two_optional_args(self):
        content = r"\citep[see][ch.~3]{lamport1994}"
        result = _parse_citations(content, "main.tex")
        assert len(result) == 1
        assert result[0].keys == ["lamport1994"]

    def test_skip_comment_lines(self):
        content = r"% \cite{commented_out}"
        result = _parse_citations(content, "main.tex")
        assert result == []

    def test_multiple_citations_same_line(self):
        content = r"See \cite{a2020} and \citep{b2021}."
        result = _parse_citations(content, "main.tex")
        assert len(result) == 2

    def test_inline_comment_hides_second_citation(self):
        """Citations after an inline % comment should be ignored."""
        content = r"\cite{real} % \cite{commented_out}"
        result = _parse_citations(content, "main.tex")
        assert len(result) == 1
        assert result[0].keys == ["real"]

    def test_no_citations(self):
        content = "No citations here.\n"
        result = _parse_citations(content, "main.tex")
        assert result == []


# ---------------------------------------------------------------------------
# Full integration: parse_bibliography
# ---------------------------------------------------------------------------


class TestParseBibliography:
    """Integration tests for parse_bibliography."""

    def _setup_project(self, tmp_path):
        """Create a project with .bib and .tex files."""
        bib = tmp_path / "refs.bib"
        bib.write_text(
            '@article{knuth1984,\n'
            '  author = {Donald Knuth},\n'
            '  title = {Literate Programming},\n'
            '  year = {1984},\n'
            '}\n'
            '\n'
            '@book{lamport1994,\n'
            '  author = {Leslie Lamport},\n'
            '  title = {LaTeX},\n'
            '  year = {1994},\n'
            '}\n'
            '\n'
            '@article{unused2020,\n'
            '  author = {Nobody},\n'
            '  title = {Unused Entry},\n'
            '  year = {2020},\n'
            '}\n'
        )

        main = tmp_path / "main.tex"
        main.write_text(
            '\\documentclass{article}\n'
            '\\begin{document}\n'
            'See \\cite{knuth1984} and \\citep{lamport1994}.\n'
            'Also \\cite{undefined2021}.\n'
            '\\end{document}\n'
        )
        return main

    def test_entries_found(self, tmp_path):
        main = self._setup_project(tmp_path)
        bib = parse_bibliography(main, tmp_path)
        assert len(bib.entries) == 3
        keys = {e.key for e in bib.entries}
        assert keys == {"knuth1984", "lamport1994", "unused2020"}

    def test_citations_found(self, tmp_path):
        main = self._setup_project(tmp_path)
        bib = parse_bibliography(main, tmp_path)
        assert len(bib.citations) == 3
        commands = [c.command for c in bib.citations]
        assert "cite" in commands
        assert "citep" in commands

    def test_uncited_keys(self, tmp_path):
        main = self._setup_project(tmp_path)
        bib = parse_bibliography(main, tmp_path)
        assert "unused2020" in bib.uncited_keys

    def test_undefined_keys(self, tmp_path):
        main = self._setup_project(tmp_path)
        bib = parse_bibliography(main, tmp_path)
        assert "undefined2021" in bib.undefined_keys

    def test_empty_project(self, tmp_path):
        main = tmp_path / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
        bib = parse_bibliography(main, tmp_path)
        assert bib.entries == []
        assert bib.citations == []
        assert bib.uncited_keys == []
        assert bib.undefined_keys == []

    def test_all_cited(self, tmp_path):
        """When all bib entries are cited and all citations are defined."""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text(
            '@article{a2020,\n  author={A},\n  year={2020},\n}\n'
        )
        main = tmp_path / "main.tex"
        main.write_text('\\cite{a2020}\n')
        bib = parse_bibliography(main, tmp_path)
        assert bib.uncited_keys == []
        assert bib.undefined_keys == []

    def test_multiple_bib_files(self, tmp_path):
        """Parse entries from multiple .bib files."""
        (tmp_path / "refs1.bib").write_text(
            '@article{a2020,\n  author={A},\n  year={2020},\n}\n'
        )
        (tmp_path / "refs2.bib").write_text(
            '@article{b2021,\n  author={B},\n  year={2021},\n}\n'
        )
        main = tmp_path / "main.tex"
        main.write_text('\\cite{a2020,b2021}\n')
        bib = parse_bibliography(main, tmp_path)
        assert len(bib.entries) == 2
        assert bib.uncited_keys == []
        assert bib.undefined_keys == []


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


class TestBibFieldPhantomRegression:
    """Regression tests for phantom field matches inside field values."""

    def test_field_value_containing_equals_pattern(self):
        """A field value containing 'word = {text}' should not create phantom field."""
        content = (
            '@article{test2020,\n'
            '  title = {The note = {nested} problem},\n'
            '  year = {2020},\n'
            '}\n'
        )
        from texwatch.bibliography import _parse_bib_file
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        # Should have title and year, but NOT a phantom "note" field
        assert "title" in result[0].fields
        assert "year" in result[0].fields
        assert "note" not in result[0].fields
        assert result[0].fields["title"] == "The note = {nested} problem"

    def test_field_value_with_multiple_phantom_patterns(self):
        """Multiple 'word = {text}' patterns inside a value should not create fields."""
        content = (
            '@article{test2020,\n'
            '  abstract = {We show that x = {1} and y = {2} hold},\n'
            '  year = {2020},\n'
            '}\n'
        )
        from texwatch.bibliography import _parse_bib_file
        result = _parse_bib_file(content, "refs.bib")
        assert len(result) == 1
        assert set(result[0].fields.keys()) == {"abstract", "year"}

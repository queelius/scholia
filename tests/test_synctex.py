"""Tests for synctex module."""

import gzip
import logging
import tempfile
from pathlib import Path

import pytest

from texwatch.synctex import (
    PDFPosition,
    SourcePosition,
    SyncTeXData,
    find_synctex_file,
    get_visible_lines,
    page_to_source,
    parse_synctex,
    source_to_page,
)


# Sample SyncTeX content (simplified)
SAMPLE_SYNCTEX = """SyncTeX Version:1
Input:1:./main.tex
Input:2:./chapter1.tex
Output:main.pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
{1
[1,42,0:0,0:0,0,0
h1,1,0:6553600,39321600:0,0
h1,10,0:6553600,45875200:0,0
h2,5,0:6553600,52428800:0,0
]
}
{2
[2,100,0:0,0:0,0,0
h1,50,0:6553600,39321600:0,0
]
}
"""


@pytest.fixture
def synctex_file(tmp_path):
    """Create a test synctex file."""
    synctex_path = tmp_path / "main.synctex.gz"
    with gzip.open(synctex_path, "wt") as f:
        f.write(SAMPLE_SYNCTEX)
    return synctex_path


@pytest.fixture
def synctex_data():
    """Create sample SyncTeXData."""
    return SyncTeXData(
        pdf_to_source={
            1: [
                (600.0, SourcePosition(file="main.tex", line=1)),
                (700.0, SourcePosition(file="main.tex", line=10)),
                (800.0, SourcePosition(file="chapter1.tex", line=5)),
            ],
            2: [
                (600.0, SourcePosition(file="main.tex", line=50)),
            ],
        },
        source_to_pdf={
            ("main.tex", 1): [PDFPosition(page=1, x=100.0, y=600.0)],
            ("main.tex", 10): [PDFPosition(page=1, x=100.0, y=700.0)],
            ("main.tex", 50): [PDFPosition(page=2, x=100.0, y=600.0)],
            ("chapter1.tex", 5): [PDFPosition(page=1, x=100.0, y=800.0)],
        },
        input_files={1: "main.tex", 2: "chapter1.tex"},
    )


class TestParseSynctex:
    """Tests for parse_synctex function."""

    def test_parse_gzipped(self, synctex_file):
        """Test parsing gzipped synctex file."""
        data = parse_synctex(synctex_file)
        assert data is not None
        assert 1 in data.input_files
        assert 2 in data.input_files
        assert "main.tex" in data.input_files[1]
        assert "chapter1.tex" in data.input_files[2]

    def test_parse_uncompressed(self, tmp_path):
        """Test parsing uncompressed synctex file."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX)

        data = parse_synctex(synctex_path)
        assert data is not None

    def test_parse_nonexistent(self, tmp_path):
        """Test parsing nonexistent file."""
        data = parse_synctex(tmp_path / "nonexistent.synctex.gz")
        assert data is None

    def test_parse_invalid_gzip(self, tmp_path):
        """Test parsing invalid gzip file."""
        bad_path = tmp_path / "bad.synctex.gz"
        bad_path.write_bytes(b"not a gzip file")

        data = parse_synctex(bad_path)
        assert data is None


class TestFindSynctexFile:
    """Tests for find_synctex_file function."""

    def test_find_gzipped(self, tmp_path):
        """Test finding gzipped synctex file."""
        pdf_path = tmp_path / "document.pdf"
        synctex_path = tmp_path / "document.synctex.gz"
        pdf_path.touch()
        synctex_path.touch()

        result = find_synctex_file(pdf_path)
        assert result == synctex_path

    def test_find_uncompressed(self, tmp_path):
        """Test finding uncompressed synctex file."""
        pdf_path = tmp_path / "document.pdf"
        synctex_path = tmp_path / "document.synctex"
        pdf_path.touch()
        synctex_path.touch()

        result = find_synctex_file(pdf_path)
        assert result == synctex_path

    def test_prefer_gzipped(self, tmp_path):
        """Test that gzipped is preferred over uncompressed."""
        pdf_path = tmp_path / "document.pdf"
        gz_path = tmp_path / "document.synctex.gz"
        raw_path = tmp_path / "document.synctex"
        pdf_path.touch()
        gz_path.touch()
        raw_path.touch()

        result = find_synctex_file(pdf_path)
        assert result == gz_path

    def test_not_found(self, tmp_path):
        """Test when no synctex file exists."""
        pdf_path = tmp_path / "document.pdf"
        pdf_path.touch()

        result = find_synctex_file(pdf_path)
        assert result is None


class TestSourceToPage:
    """Tests for source_to_page function."""

    def test_exact_match(self, synctex_data):
        """Test finding exact line match."""
        pos = source_to_page(synctex_data, "main.tex", 10)
        assert pos is not None
        assert pos.page == 1
        assert pos.y == 700.0

    def test_different_file(self, synctex_data):
        """Test finding line in different file."""
        pos = source_to_page(synctex_data, "chapter1.tex", 5)
        assert pos is not None
        assert pos.page == 1

    def test_nearest_line(self, synctex_data):
        """Test finding nearest line when exact not found."""
        pos = source_to_page(synctex_data, "main.tex", 8)
        assert pos is not None
        # Should find line 10 (closest to 8 among 1, 10, 50)
        assert pos.page == 1

    def test_not_found(self, synctex_data):
        """Test when file not in synctex data."""
        pos = source_to_page(synctex_data, "nonexistent.tex", 1)
        assert pos is None


class TestPageToSource:
    """Tests for page_to_source function."""

    def test_page_only(self, synctex_data):
        """Test finding source for page without y."""
        pos = page_to_source(synctex_data, 1)
        assert pos is not None
        assert pos.file == "main.tex"
        assert pos.line == 1  # First entry on page

    def test_page_with_y(self, synctex_data):
        """Test finding source for page with y coordinate."""
        pos = page_to_source(synctex_data, 1, y=750.0)
        assert pos is not None
        # 750 is closer to 700 (line 10) than 600 (line 1) or 800 (chapter1:5)
        assert pos.line == 10

    def test_page_not_found(self, synctex_data):
        """Test when page not in synctex data."""
        pos = page_to_source(synctex_data, 99)
        assert pos is None


class TestGetVisibleLines:
    """Tests for get_visible_lines function."""

    def test_single_page(self, synctex_data):
        """Test getting visible lines for a page."""
        result = get_visible_lines(synctex_data, 1)
        assert result is not None
        min_line, max_line = result
        # Page 1 has lines 1, 10 from main.tex and 5 from chapter1.tex
        assert min_line == 1
        assert max_line == 10

    def test_page_not_found(self, synctex_data):
        """Test when page not in synctex data."""
        result = get_visible_lines(synctex_data, 99)
        assert result is None


class TestSyncTeXLogging:
    """Tests for debug logging in synctex functions."""

    def test_source_to_page_logs_exact_match(self, synctex_data, caplog):
        """Test that exact match is logged."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            source_to_page(synctex_data, "main.tex", 10)
        assert "EXACT match" in caplog.text

    def test_source_to_page_logs_nearest_match(self, synctex_data, caplog):
        """Test that nearest match is logged with delta."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            source_to_page(synctex_data, "main.tex", 8)
        assert "NEAREST match" in caplog.text
        assert "delta=" in caplog.text

    def test_source_to_page_logs_no_match(self, synctex_data, caplog):
        """Test that no-match is logged."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            source_to_page(synctex_data, "nonexistent.tex", 1)
        assert "NO MATCH" in caplog.text

    def test_page_to_source_logs_closest_y(self, synctex_data, caplog):
        """Test that closest y lookup is logged."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            page_to_source(synctex_data, 1, y=650.0)
        assert "closest y=" in caplog.text
        assert "delta=" in caplog.text

    def test_page_to_source_logs_missing_page(self, synctex_data, caplog):
        """Test that missing page is logged."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            page_to_source(synctex_data, 99)
        assert "page NOT in data" in caplog.text

    def test_parse_synctex_logs_stats(self, synctex_file, caplog):
        """Test that parse_synctex logs file and page counts."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            parse_synctex(synctex_file)
        assert "input file" in caplog.text.lower()
        assert "pages" in caplog.text

    def test_parse_synctex_logs_failure(self, tmp_path, caplog):
        """Test that parse_synctex logs read failure."""
        with caplog.at_level(logging.DEBUG, logger="texwatch.synctex"):
            parse_synctex(tmp_path / "nonexistent.synctex.gz")
        assert "failed to read" in caplog.text

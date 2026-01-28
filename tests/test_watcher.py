"""Tests for watcher module."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from texwatch.watcher import TexFileHandler, TexWatcher


class TestTexFileHandler:
    """Tests for TexFileHandler class."""

    def test_matches_simple_pattern(self):
        """Test matching simple glob pattern."""
        loop = asyncio.new_event_loop()
        handler = TexFileHandler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._matches_patterns("main.tex", ["*.tex"])
        assert handler._matches_patterns("chapter.tex", ["*.tex"])
        assert not handler._matches_patterns("main.pdf", ["*.tex"])

        loop.close()

    def test_matches_directory_pattern(self):
        """Test matching directory glob pattern."""
        loop = asyncio.new_event_loop()
        handler = TexFileHandler(
            watch_patterns=["sections/*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._matches_patterns("sections/intro.tex", ["sections/*.tex"])
        assert not handler._matches_patterns("chapters/intro.tex", ["sections/*.tex"])

        loop.close()

    def test_should_process_tex_file(self):
        """Test that .tex files are processed."""
        loop = asyncio.new_event_loop()
        handler = TexFileHandler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._should_process("main.tex")
        assert handler._should_process("chapter.tex")

        loop.close()

    def test_should_ignore_aux_files(self):
        """Test that auxiliary files are ignored."""
        loop = asyncio.new_event_loop()
        handler = TexFileHandler(
            watch_patterns=["*"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert not handler._should_process("main.aux")
        assert not handler._should_process("main.log")
        assert not handler._should_process("main.pdf")
        assert not handler._should_process("main.synctex.gz")
        assert not handler._should_process("main.fdb_latexmk")

        loop.close()

    def test_should_respect_ignore_patterns(self):
        """Test that ignore patterns are respected."""
        loop = asyncio.new_event_loop()
        handler = TexFileHandler(
            watch_patterns=["*.tex"],
            ignore_patterns=["*_backup.tex", "old/*"],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._should_process("main.tex")
        assert not handler._should_process("main_backup.tex")
        assert not handler._should_process("old/chapter.tex")

        loop.close()


class TestTexWatcher:
    """Tests for TexWatcher class."""

    def test_init(self, tmp_path):
        """Test TexWatcher initialization."""
        callback = AsyncMock()
        watcher = TexWatcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=callback,
        )

        assert watcher.watch_dir == tmp_path
        assert watcher.watch_patterns == ["*.tex"]
        assert watcher.on_change == callback
        assert not watcher.is_running

    def test_start_stop(self, tmp_path):
        """Test starting and stopping watcher."""
        callback = AsyncMock()
        watcher = TexWatcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=callback,
        )

        loop = asyncio.new_event_loop()

        watcher.start(loop)
        assert watcher.is_running

        watcher.stop()
        assert not watcher.is_running

        loop.close()

    def test_is_running_property(self, tmp_path):
        """Test is_running property."""
        watcher = TexWatcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=AsyncMock(),
        )

        assert not watcher.is_running

        loop = asyncio.new_event_loop()
        watcher.start(loop)
        assert watcher.is_running

        watcher.stop()
        loop.close()

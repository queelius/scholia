"""Tests for CLI module."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import pytest

from texwatch.cli import main, get_status, send_goto


class TestCLI:
    """Tests for CLI commands."""

    def test_version(self, capsys):
        """Test --version flag."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "texwatch" in captured.out

    def test_init_creates_config(self, tmp_path, monkeypatch):
        """Test --init creates texwatch.yaml."""
        monkeypatch.chdir(tmp_path)

        # Create a .tex file for init to find
        (tmp_path / "document.tex").touch()

        result = main(["--init"])
        assert result == 0
        assert (tmp_path / "texwatch.yaml").exists()

    def test_init_with_existing_config(self, tmp_path, monkeypatch, capsys):
        """Test --init with existing config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "texwatch.yaml").write_text("main: test.tex\n")

        result = main(["--init"])
        assert result == 1

        captured = capsys.readouterr()
        assert "already exists" in captured.out

    def test_init_force_overwrites(self, tmp_path, monkeypatch):
        """Test --init --force overwrites existing config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "texwatch.yaml").write_text("main: old.tex\n")
        (tmp_path / "new.tex").touch()

        result = main(["--init", "--force"])
        assert result == 0

    def test_status_no_server(self, capsys):
        """Test --status when no server running."""
        result = main(["--status", "--port", "59999"])
        assert result == 1

        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_goto_no_server(self, capsys):
        """Test --goto when no server running."""
        result = main(["--goto", "42", "--port", "59999"])
        assert result == 1

        captured = capsys.readouterr()
        assert "Failed to navigate" in captured.out

    def test_run_missing_main_file(self, tmp_path, monkeypatch, capsys):
        """Test run with missing main file."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "texwatch.yaml").write_text("main: nonexistent.tex\n")

        result = main([])
        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_run_missing_compiler(self, tmp_path, monkeypatch, capsys):
        """Test run with missing compiler."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        (tmp_path / "texwatch.yaml").write_text(
            "main: main.tex\ncompiler: nonexistent_compiler_xyz\n"
        )

        result = main([])
        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.out


class TestAPIHelpers:
    """Tests for API helper functions."""

    def test_get_status_no_server(self):
        """Test get_status when no server."""
        result = get_status(port=59999)
        assert result is None

    def test_send_goto_no_server(self):
        """Test send_goto when no server."""
        result = send_goto("42", port=59999)
        assert result is False


class MockHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for testing."""

    def log_message(self, format, *args):
        pass  # Suppress logging

    def do_GET(self):
        if self.path == "/status":
            response = {
                "file": "main.tex",
                "compiling": False,
                "success": True,
                "errors": [],
                "warnings": [],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/goto":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
        else:
            self.send_response(404)
            self.end_headers()


class TestWithMockServer:
    """Tests with a mock server."""

    @pytest.fixture
    def mock_server(self):
        """Create and start a mock server using serve_forever for clean shutdown."""
        server = HTTPServer(("localhost", 0), MockHandler)
        server.timeout = 0.1
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_get_status_with_server(self, mock_server):
        """Test get_status with running server."""
        result = get_status(port=mock_server)
        assert result is not None
        assert result["file"] == "main.tex"

    def test_send_goto_with_server(self, mock_server):
        """Test send_goto with running server."""
        result = send_goto("42", port=mock_server)
        assert result is True

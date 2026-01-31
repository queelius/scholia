"""Tests for CLI module."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import pytest
import yaml

from texwatch.cli import (
    main,
    get_status,
    send_goto,
    GotoResult,
    APIResponse,
    _api_get,
    _api_post,
    EXIT_OK,
    EXIT_FAIL,
    EXIT_SERVER_DOWN,
    build_parser,
    _DISPATCH,
    cmd_view,
)


# ---------------------------------------------------------------------------
# Test: CLI subcommand parsing
# ---------------------------------------------------------------------------


class TestSubcommandParsing:
    """Tests for the subcommand-based argument parser."""

    def test_version(self, capsys):
        """Test --version flag."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "texwatch" in captured.out

    def test_default_command_is_run(self):
        """Test that no subcommand defaults to run."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_init_subcommand_parsed(self):
        """Test init subcommand parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["init"])
        assert args.command == "init"

    def test_init_force_flag(self):
        """Test init --force."""
        parser = build_parser()
        args = parser.parse_args(["init", "--force"])
        assert args.command == "init"
        assert args.force is True

    def test_init_compiler_flag(self):
        """Test init --compiler."""
        parser = build_parser()
        args = parser.parse_args(["init", "--compiler", "xelatex"])
        assert args.command == "init"
        assert args.compiler == "xelatex"

    def test_status_subcommand(self):
        """Test status subcommand."""
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_status_json_flag(self):
        """Test status --json."""
        parser = build_parser()
        args = parser.parse_args(["status", "--json"])
        assert args.command == "status"
        assert args.json is True

    def test_goto_subcommand(self):
        """Test goto subcommand with target."""
        parser = build_parser()
        args = parser.parse_args(["goto", "42"])
        assert args.command == "goto"
        assert args.target == "42"

    def test_compile_subcommand(self):
        """Test compile subcommand."""
        parser = build_parser()
        args = parser.parse_args(["compile"])
        assert args.command == "compile"

    def test_capture_subcommand(self):
        """Test capture subcommand with output."""
        parser = build_parser()
        args = parser.parse_args(["capture", "out.png"])
        assert args.command == "capture"
        assert args.output == "out.png"

    def test_capture_page_dpi_scoped(self):
        """Test --page and --dpi are scoped to capture only."""
        parser = build_parser()
        args = parser.parse_args(["capture", "out.png", "--page", "3", "--dpi", "200"])
        assert args.page == 3
        assert args.dpi == 200

    def test_config_subcommand_default_show(self):
        """Test config defaults to show action."""
        parser = build_parser()
        args = parser.parse_args(["config"])
        assert args.command == "config"
        assert args.action == "show"

    def test_config_set_action(self):
        """Test config set action."""
        parser = build_parser()
        args = parser.parse_args(["config", "set", "compiler", "xelatex"])
        assert args.action == "set"
        assert args.key == "compiler"
        assert args.value == "xelatex"

    def test_config_add_action(self):
        """Test config add action."""
        parser = build_parser()
        args = parser.parse_args(["config", "add", "watch", "*.bib"])
        assert args.action == "add"
        assert args.key == "watch"
        assert args.value == "*.bib"

    def test_config_remove_action(self):
        """Test config remove action."""
        parser = build_parser()
        args = parser.parse_args(["config", "remove", "ignore", "*.tmp"])
        assert args.action == "remove"
        assert args.key == "ignore"
        assert args.value == "*.tmp"

    def test_config_path_action(self):
        """Test config path action."""
        parser = build_parser()
        args = parser.parse_args(["config", "path"])
        assert args.action == "path"

    def test_files_subcommand(self):
        """Test files subcommand."""
        parser = build_parser()
        args = parser.parse_args(["files"])
        assert args.command == "files"

    def test_files_json_flag(self):
        """Test files --json."""
        parser = build_parser()
        args = parser.parse_args(["files", "--json"])
        assert args.json is True

    def test_dispatch_table_complete(self):
        """Test that dispatch table covers all subcommands."""
        expected = {
            None, "init", "status", "view", "goto", "compile", "capture",
            "config", "files", "scan", "serve", "mcp",
        }
        assert set(_DISPATCH.keys()) == expected

    def test_debug_flag_global(self):
        """Test --debug is a global flag."""
        parser = build_parser()
        args = parser.parse_args(["--debug"])
        assert args.debug is True

    def test_port_per_subcommand(self):
        """Test each server subcommand has its own --port."""
        parser = build_parser()
        for cmd in ["status", "view", "goto 42", "compile", "capture out.png", "files"]:
            args = parser.parse_args(cmd.split() + ["--port", "9999"])
            assert args.port == 9999


# ---------------------------------------------------------------------------
# Test: Shared HTTP client
# ---------------------------------------------------------------------------


class TestAPIHelpers:
    """Tests for the shared _api_get and _api_post helpers."""

    def test_api_get_server_down(self):
        """Test _api_get when no server is running."""
        resp = _api_get("/status", port=59999)
        assert resp.server_down is True
        assert resp.status == 0

    def test_api_post_server_down(self):
        """Test _api_post when no server is running."""
        resp = _api_post("/goto", {"line": 1}, port=59999)
        assert resp.server_down is True
        assert resp.status == 0

    def test_api_response_dataclass(self):
        """Test APIResponse defaults."""
        r = APIResponse(status=200, data={"ok": True})
        assert r.status == 200
        assert r.data == {"ok": True}
        assert r.error is None
        assert r.server_down is False


class TestLegacyHelpers:
    """Tests for get_status and send_goto helpers."""

    def test_get_status_no_server(self):
        """Test get_status when no server."""
        result = get_status(port=59999)
        assert result is None

    def test_send_goto_no_server(self):
        """Test send_goto when no server."""
        result = send_goto("42", port=59999)
        assert isinstance(result, GotoResult)
        assert result.success is False
        assert result.server_down is True

    def test_goto_result_dataclass(self):
        """Test GotoResult dataclass defaults."""
        r = GotoResult(success=True)
        assert r.success is True
        assert r.error is None
        assert r.server_down is False


# ---------------------------------------------------------------------------
# Test: cmd_init
# ---------------------------------------------------------------------------


class TestCmdInit:
    """Tests for the init subcommand."""

    def test_init_creates_config(self, tmp_path, monkeypatch):
        """Test init creates .texwatch.yaml."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "document.tex").touch()

        result = main(["init"])
        assert result == EXIT_OK
        assert (tmp_path / ".texwatch.yaml").exists()

    def test_init_with_existing_config(self, tmp_path, monkeypatch, capsys):
        """Test init with existing config fails."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: test.tex\n")

        result = main(["init"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "already exists" in captured.out

    def test_init_force_overwrites(self, tmp_path, monkeypatch):
        """Test init --force overwrites existing config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: old.tex\n")
        (tmp_path / "new.tex").touch()

        result = main(["init", "--force"])
        assert result == EXIT_OK

    def test_init_with_compiler(self, tmp_path, monkeypatch):
        """Test init --compiler sets compiler field."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "doc.tex").touch()

        result = main(["init", "--compiler", "xelatex"])
        assert result == EXIT_OK
        with open(tmp_path / ".texwatch.yaml") as f:
            data = yaml.safe_load(f)
        assert data["compiler"] == "xelatex"


# ---------------------------------------------------------------------------
# Test: cmd_status
# ---------------------------------------------------------------------------


class TestCmdStatus:
    """Tests for the status subcommand."""

    def test_status_no_server(self, capsys):
        """Test status when no server running returns exit code 3."""
        result = main(["status", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_status_no_server_json(self, capsys):
        """Test status --json when no server returns JSON with exit code 3."""
        result = main(["status", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"


# ---------------------------------------------------------------------------
# Test: cmd_view
# ---------------------------------------------------------------------------


class TestCmdView:
    """Tests for the view subcommand."""

    def test_view_no_server(self, capsys):
        """Test view when no server running returns exit code 3."""
        result = main(["view", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_view_no_server_json(self, capsys):
        """Test view --json when no server returns JSON with exit code 3."""
        result = main(["view", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"

    def test_view_subcommand_parsed(self):
        """Test view subcommand parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["view"])
        assert args.command == "view"

    def test_view_json_flag(self):
        """Test view --json flag."""
        parser = build_parser()
        args = parser.parse_args(["view", "--json"])
        assert args.json is True


class TestHelpFlag:
    """Tests for --help flag fix."""

    def test_help_flag(self, capsys):
        """Test that texwatch --help shows help text and exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "texwatch" in captured.out
        assert "view" in captured.out

    def test_h_flag(self, capsys):
        """Test that texwatch -h shows help text and exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["-h"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "texwatch" in captured.out


# ---------------------------------------------------------------------------
# Test: cmd_goto
# ---------------------------------------------------------------------------


class TestCmdGoto:
    """Tests for the goto subcommand."""

    def test_goto_no_server(self, capsys):
        """Test goto when no server returns exit code 3."""
        result = main(["goto", "42", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "Failed to navigate" in captured.out
        assert "Is texwatch running?" in captured.out

    def test_goto_no_server_json(self, capsys):
        """Test goto --json when no server returns JSON with exit code 3."""
        result = main(["goto", "42", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"


# ---------------------------------------------------------------------------
# Test: cmd_compile
# ---------------------------------------------------------------------------


class TestCmdCompile:
    """Tests for the compile subcommand."""

    def test_compile_no_server(self, capsys):
        """Test compile when no server returns exit code 3."""
        result = main(["compile", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_compile_no_server_json(self, capsys):
        """Test compile --json when no server returns JSON with exit code 3."""
        result = main(["compile", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"


# ---------------------------------------------------------------------------
# Test: cmd_capture
# ---------------------------------------------------------------------------


class TestCmdCapture:
    """Tests for the capture subcommand."""

    def test_capture_no_server(self, capsys, tmp_path):
        """Test capture when no server returns exit code 3."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "Is texwatch running?" in captured.out

    def test_capture_no_server_json(self, capsys, tmp_path):
        """Test capture --json when no server returns JSON with exit code 3."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"


# ---------------------------------------------------------------------------
# Test: cmd_files
# ---------------------------------------------------------------------------


class TestCmdFiles:
    """Tests for the files subcommand."""

    def test_files_no_server(self, capsys):
        """Test files when no server returns exit code 3."""
        result = main(["files", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_files_no_server_json(self, capsys):
        """Test files --json when no server returns JSON with exit code 3."""
        result = main(["files", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"


# ---------------------------------------------------------------------------
# Test: cmd_config
# ---------------------------------------------------------------------------


class TestCmdConfig:
    """Tests for the config subcommand."""

    def test_config_show_no_file(self, tmp_path, monkeypatch, capsys):
        """Test config show when no .texwatch.yaml exists."""
        monkeypatch.chdir(tmp_path)
        result = main(["config"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "No .texwatch.yaml found" in captured.out

    def test_config_show_no_file_json(self, tmp_path, monkeypatch, capsys):
        """Test config show --json when no .texwatch.yaml."""
        monkeypatch.chdir(tmp_path)
        result = main(["config", "show", "--json"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data

    def test_config_show(self, tmp_path, monkeypatch, capsys):
        """Test config show displays config contents."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\ncompiler: xelatex\n")

        result = main(["config"])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "doc.tex" in captured.out

    def test_config_show_json(self, tmp_path, monkeypatch, capsys):
        """Test config show --json outputs valid JSON."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\ncompiler: xelatex\n")

        result = main(["config", "show", "--json"])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["main"] == "doc.tex"
        assert data["compiler"] == "xelatex"
        assert "_path" in data

    def test_config_path(self, tmp_path, monkeypatch, capsys):
        """Test config path prints config file path."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "path"])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert ".texwatch.yaml" in captured.out

    def test_config_path_no_file(self, tmp_path, monkeypatch, capsys):
        """Test config path when no config exists."""
        monkeypatch.chdir(tmp_path)
        result = main(["config", "path"])
        assert result == EXIT_FAIL

    def test_config_set_scalar(self, tmp_path, monkeypatch, capsys):
        """Test config set on a scalar field."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\ncompiler: auto\n")

        result = main(["config", "set", "compiler", "xelatex"])
        assert result == EXIT_OK

        with open(tmp_path / ".texwatch.yaml") as f:
            data = yaml.safe_load(f)
        assert data["compiler"] == "xelatex"

    def test_config_set_port(self, tmp_path, monkeypatch, capsys):
        """Test config set port coerces to int."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\nport: 8765\n")

        result = main(["config", "set", "port", "9999"])
        assert result == EXIT_OK

        with open(tmp_path / ".texwatch.yaml") as f:
            data = yaml.safe_load(f)
        assert data["port"] == 9999

    def test_config_set_port_invalid(self, tmp_path, monkeypatch, capsys):
        """Test config set port with non-numeric value."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "set", "port", "abc"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Invalid port" in captured.out

    def test_config_set_list_field_rejects(self, tmp_path, monkeypatch, capsys):
        """Test config set on a list field is rejected."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "set", "watch", "*.bib"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "list field" in captured.out

    def test_config_add_to_list(self, tmp_path, monkeypatch, capsys):
        """Test config add appends to list field."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\nwatch:\n- '*.tex'\n")

        result = main(["config", "add", "watch", "*.bib"])
        assert result == EXIT_OK

        with open(tmp_path / ".texwatch.yaml") as f:
            data = yaml.safe_load(f)
        assert "*.bib" in data["watch"]
        assert "*.tex" in data["watch"]

    def test_config_add_no_duplicates(self, tmp_path, monkeypatch, capsys):
        """Test config add doesn't add duplicates."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\nwatch:\n- '*.tex'\n")

        result = main(["config", "add", "watch", "*.tex"])
        assert result == EXIT_OK

        with open(tmp_path / ".texwatch.yaml") as f:
            data = yaml.safe_load(f)
        assert data["watch"].count("*.tex") == 1

    def test_config_add_scalar_field_rejects(self, tmp_path, monkeypatch, capsys):
        """Test config add on a scalar field is rejected."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "add", "compiler", "xelatex"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "scalar field" in captured.out

    def test_config_remove_from_list(self, tmp_path, monkeypatch, capsys):
        """Test config remove removes from list field."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text(
            "main: doc.tex\nwatch:\n- '*.tex'\n- '*.bib'\n"
        )

        result = main(["config", "remove", "watch", "*.bib"])
        assert result == EXIT_OK

        with open(tmp_path / ".texwatch.yaml") as f:
            data = yaml.safe_load(f)
        assert "*.bib" not in data["watch"]
        assert "*.tex" in data["watch"]

    def test_config_remove_missing_value(self, tmp_path, monkeypatch, capsys):
        """Test config remove fails if value not present."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\nwatch:\n- '*.tex'\n")

        result = main(["config", "remove", "watch", "*.bib"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_config_unknown_field(self, tmp_path, monkeypatch, capsys):
        """Test config set with unknown field."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "set", "nonexistent", "val"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Unknown config field" in captured.out

    def test_config_set_missing_value(self, tmp_path, monkeypatch, capsys):
        """Test config set without value argument."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "set", "compiler"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "requires a VALUE" in captured.out

    def test_config_set_missing_key(self, tmp_path, monkeypatch, capsys):
        """Test config set without key argument."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: doc.tex\n")

        result = main(["config", "set"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "requires a KEY" in captured.out


# ---------------------------------------------------------------------------
# Test: cmd_run
# ---------------------------------------------------------------------------


class TestCmdServeDefault:
    """Tests for the default serve behavior (no subcommand)."""

    def test_serve_no_yaml_default(self, tmp_path, monkeypatch, capsys):
        """Test default serve with no .texwatch.yaml."""
        monkeypatch.chdir(tmp_path)
        result = main([])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "No .texwatch.yaml found" in captured.out

    def test_serve_missing_compiler(self, tmp_path, monkeypatch, capsys):
        """Test default serve with missing compiler."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")
        (tmp_path / ".texwatch.yaml").write_text(
            "main: main.tex\ncompiler: nonexistent_compiler_xyz\n"
        )

        result = main([])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "not found" in captured.out


class TestDebugFlag:
    """Tests for --debug flag."""

    def test_debug_flag_parsed(self):
        """Test --debug is accepted by the argument parser."""
        parser = build_parser()
        args = parser.parse_args(["--debug"])
        assert args.debug is True

    def test_debug_flag_with_status(self):
        """Test --debug works alongside status subcommand."""
        # --debug is global, status has its own port
        result = main(["--debug", "status", "--port", "59999"])
        # Returns EXIT_SERVER_DOWN because no server, but no parse error
        assert result == EXIT_SERVER_DOWN


class TestPortConflict:
    """Tests for port-already-in-use handling via CLI."""

    def test_main_returns_1_on_port_conflict(self, tmp_path, monkeypatch, capsys):
        """Test that main() returns 1 on port conflict."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n"
        )
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\ncompiler: latexmk\n")

        with patch("texwatch.cli.TexWatchServer") as MockServer:
            instance = MockServer.return_value
            instance.run.side_effect = SystemExit(1)
            result = main(["--port", "9999"])

        assert result == EXIT_FAIL


# ---------------------------------------------------------------------------
# Mock HTTP handlers
# ---------------------------------------------------------------------------


class MockHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for testing."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/status":
            response = {
                "file": "main.tex",
                "compiling": False,
                "success": True,
                "errors": [],
                "warnings": [],
                "viewer": {
                    "page": 3,
                    "total_pages": 15,
                    "visible_lines": None,
                },
                "editor": {
                    "file": "chapters/intro.tex",
                    "line": 42,
                },
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        elif self.path.startswith("/capture"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        elif self.path == "/files":
            response = {
                "entries": [
                    {"name": "main.tex", "type": "file"},
                    {"name": "chapters", "type": "directory", "children": [
                        {"name": "intro.tex", "type": "file"},
                    ]},
                ]
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
        elif self.path == "/compile":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True,
                "errors": [],
                "warnings": [],
                "duration_seconds": 1.5,
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()


class MockErrorHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler that returns errors for testing."""

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if self.path == "/goto":
            body = json.dumps({"error": "Line not found in PDF"}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/compile":
            body = json.dumps({
                "success": False,
                "errors": [{"file": "main.tex", "line": 10, "message": "Undefined control sequence"}],
                "warnings": [],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith("/capture"):
            body = json.dumps({"error": "pymupdf not installed. Run: pip install texwatch[capture]"}).encode()
            self.send_response(501)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


# ---------------------------------------------------------------------------
# Test: Mock server (success paths)
# ---------------------------------------------------------------------------


class TestWithMockServer:
    """Tests with a mock server."""

    @pytest.fixture
    def mock_server(self):
        """Create and start a mock server."""
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
        assert isinstance(result, GotoResult)
        assert result.success is True
        assert result.server_down is False

    def test_cmd_status_success(self, mock_server, capsys):
        """Test status with running server."""
        result = main(["status", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "File: main.tex" in captured.out

    def test_cmd_status_json(self, mock_server, capsys):
        """Test status --json outputs valid JSON."""
        result = main(["status", "--json", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["file"] == "main.tex"
        assert data["success"] is True

    def test_cmd_goto_success(self, mock_server, capsys):
        """Test goto with successful navigation."""
        result = main(["goto", "42", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Navigated to: 42" in captured.out

    def test_cmd_goto_json(self, mock_server, capsys):
        """Test goto --json outputs valid JSON."""
        result = main(["goto", "42", "--json", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert data["target"] == "42"

    def test_cmd_capture_success(self, mock_server, tmp_path, capsys):
        """Test capture with running server returning PNG."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Saved:" in captured.out
        assert Path(output).exists()

    def test_cmd_capture_json(self, mock_server, tmp_path, capsys):
        """Test capture --json outputs valid JSON."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--json", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["saved"] == output
        assert "bytes" in data

    def test_cmd_compile_success(self, mock_server, capsys):
        """Test compile with successful compilation."""
        result = main(["compile", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Compile successful" in captured.out
        assert "Duration: 1.5s" in captured.out

    def test_cmd_compile_json(self, mock_server, capsys):
        """Test compile --json outputs valid JSON."""
        result = main(["compile", "--json", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert data["duration_seconds"] == 1.5

    def test_cmd_view_success(self, mock_server, capsys):
        """Test view with running server shows human-readable output."""
        result = main(["view", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Main file: main.tex" in captured.out
        assert "Editor pane:" in captured.out
        assert "chapters/intro.tex" in captured.out
        assert "Line: 42" in captured.out
        assert "Viewer pane:" in captured.out
        assert "Page: 3/15" in captured.out

    def test_cmd_view_json(self, mock_server, capsys):
        """Test view --json outputs valid JSON with correct structure."""
        result = main(["view", "--json", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["main_file"] == "main.tex"
        assert data["editor"]["file"] == "chapters/intro.tex"
        assert data["editor"]["line"] == 42
        assert data["viewer"]["page"] == 3
        assert data["viewer"]["total_pages"] == 15

    def test_cmd_files_success(self, mock_server, capsys):
        """Test files with running server."""
        result = main(["files", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "main.tex" in captured.out
        assert "chapters" in captured.out
        assert "intro.tex" in captured.out

    def test_cmd_files_json(self, mock_server, capsys):
        """Test files --json outputs valid JSON."""
        result = main(["files", "--json", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "entries" in data

    def test_cmd_files_tree_formatting(self, mock_server, capsys):
        """Test files tree uses box-drawing characters."""
        result = main(["files", "--port", str(mock_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        # Should contain tree connectors
        assert "├" in captured.out or "└" in captured.out

    def test_api_get_success(self, mock_server):
        """Test _api_get with running server."""
        resp = _api_get("/status", port=mock_server)
        assert resp.server_down is False
        assert resp.error is None
        assert isinstance(resp.data, dict)
        assert resp.data["file"] == "main.tex"

    def test_api_post_success(self, mock_server):
        """Test _api_post with running server."""
        resp = _api_post("/goto", {"line": 42}, port=mock_server)
        assert resp.server_down is False
        assert resp.error is None
        assert isinstance(resp.data, dict)
        assert resp.data["success"] is True


# ---------------------------------------------------------------------------
# Test: Mock error server (error paths)
# ---------------------------------------------------------------------------


class TestWithMockErrorServer:
    """Tests with a mock server that returns errors."""

    @pytest.fixture
    def error_server(self):
        """Create a mock server that returns HTTP errors."""
        server = HTTPServer(("localhost", 0), MockErrorHandler)
        server.timeout = 0.1
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_goto_result_http_error(self, error_server):
        """Test send_goto returns error message from server body."""
        result = send_goto("42", port=error_server)
        assert isinstance(result, GotoResult)
        assert result.success is False
        assert result.server_down is False
        assert result.error == "Line not found in PDF"

    def test_cmd_goto_shows_error_message(self, error_server, capsys):
        """Test goto shows actual error, not 'Is texwatch running?'."""
        result = main(["goto", "42", "--port", str(error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Line not found in PDF" in captured.out
        assert "Is texwatch running?" not in captured.out

    def test_cmd_goto_error_json(self, error_server, capsys):
        """Test goto --json shows error in JSON."""
        result = main(["goto", "42", "--json", "--port", str(error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False
        assert "error" in data

    def test_cmd_capture_server_error(self, error_server, tmp_path, capsys):
        """Test capture shows error message from server."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--port", str(error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "pymupdf not installed" in captured.out

    def test_cmd_capture_server_down(self, capsys, tmp_path):
        """Test capture when server is not running."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "Is texwatch running?" in captured.out

    def test_goto_section_error(self, error_server):
        """Test send_goto with section target returns error from server."""
        result = send_goto("Introduction", port=error_server)
        assert isinstance(result, GotoResult)
        assert result.success is False
        assert result.server_down is False

    def test_cmd_compile_failure(self, error_server, capsys):
        """Test compile with compile errors returns exit code 1."""
        result = main(["compile", "--port", str(error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Compile failed" in captured.out

    def test_cmd_compile_failure_json(self, error_server, capsys):
        """Test compile --json with compile errors returns JSON."""
        result = main(["compile", "--json", "--port", str(error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False
        assert len(data["errors"]) > 0

    def test_cmd_compile_failure_shows_errors(self, error_server, capsys):
        """Test compile shows individual error messages."""
        result = main(["compile", "--port", str(error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Undefined control sequence" in captured.out


# ---------------------------------------------------------------------------
# Test: New workspace commands — parsing
# ---------------------------------------------------------------------------


class TestNewSubcommandParsing:
    """Tests for scan and serve subcommand parsers."""

    def test_scan_subcommand(self):
        """Test scan subcommand parses directory."""
        parser = build_parser()
        args = parser.parse_args(["scan", "/tmp/papers"])
        assert args.command == "scan"
        assert args.directory == "/tmp/papers"

    def test_scan_json(self):
        """Test scan --json flag."""
        parser = build_parser()
        args = parser.parse_args(["scan", "/tmp", "--json"])
        assert args.json is True

    def test_scan_skip_dirs(self):
        """Test scan --skip-dirs flag."""
        parser = build_parser()
        args = parser.parse_args(["scan", "/tmp", "--skip-dirs", "build,dist"])
        assert args.skip_dirs == "build,dist"

    def test_serve_subcommand(self):
        """Test serve subcommand."""
        parser = build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"

    def test_serve_port(self):
        """Test serve --port flag."""
        parser = build_parser()
        args = parser.parse_args(["serve", "--port", "9000"])
        assert args.port == 9000

    def test_serve_dir(self):
        """Test serve --dir flag."""
        parser = build_parser()
        args = parser.parse_args(["serve", "--dir", "/tmp/papers"])
        assert args.dir == "/tmp/papers"

    def test_serve_recursive(self):
        """Test serve --recursive flag."""
        parser = build_parser()
        args = parser.parse_args(["serve", "--recursive"])
        assert args.recursive is True

    def test_serve_skip_dirs(self):
        """Test serve --skip-dirs flag."""
        parser = build_parser()
        args = parser.parse_args(["serve", "--skip-dirs", "build,dist"])
        assert args.skip_dirs == "build,dist"

    def test_project_flag_on_status(self):
        """Test --project flag on status subcommand."""
        parser = build_parser()
        args = parser.parse_args(["status", "--project", "thesis"])
        assert args.project == "thesis"

    def test_project_flag_on_compile(self):
        """Test --project flag on compile subcommand."""
        parser = build_parser()
        args = parser.parse_args(["compile", "--project", "paper"])
        assert args.project == "paper"


# ---------------------------------------------------------------------------
# Test: cmd_scan
# ---------------------------------------------------------------------------


class TestCmdScan:
    """Tests for the scan subcommand."""

    def test_scan_nonexistent_dir(self, capsys):
        """Test scan with nonexistent directory."""
        result = main(["scan", "/nonexistent/dir/xyz"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Not a directory" in captured.out

    def test_scan_empty_dir(self, tmp_path, capsys):
        """Test scan with directory containing no projects."""
        result = main(["scan", str(tmp_path)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "No projects found" in captured.out

    def test_scan_finds_yaml(self, tmp_path, capsys):
        """Test scan finds directories with .texwatch.yaml."""
        paper_dir = tmp_path / "thesis"
        paper_dir.mkdir()
        (paper_dir / ".texwatch.yaml").write_text("main: main.tex\n")
        (paper_dir / "main.tex").write_text("\\documentclass{article}\n")

        result = main(["scan", str(tmp_path)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "main.tex" in captured.out
        assert "1 projects" in captured.out or "1 director" in captured.out

    def test_scan_ignores_dirs_without_yaml(self, tmp_path, capsys):
        """Test scan ignores directories without .texwatch.yaml."""
        bare_dir = tmp_path / "bare"
        bare_dir.mkdir()
        (bare_dir / "main.tex").write_text("\\documentclass{article}\n")

        result = main(["scan", str(tmp_path)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "No projects found" in captured.out

    def test_scan_json(self, tmp_path, capsys):
        """Test scan --json outputs JSON."""
        paper_dir = tmp_path / "paper"
        paper_dir.mkdir()
        (paper_dir / ".texwatch.yaml").write_text("main: main.tex\n")
        (paper_dir / "main.tex").write_text("\\documentclass{article}\n")

        result = main(["scan", str(tmp_path), "--json"])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["main"] == "main.tex"


# ---------------------------------------------------------------------------
# Test: cmd_serve
# ---------------------------------------------------------------------------


class TestCmdServe:
    """Tests for the serve subcommand."""

    def test_serve_no_yaml(self, tmp_path, monkeypatch, capsys):
        """Test serve when no .texwatch.yaml exists hints to run init."""
        monkeypatch.chdir(tmp_path)
        result = main(["serve", "--dir", str(tmp_path)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "No .texwatch.yaml found" in captured.out
        assert "texwatch init" in captured.out

    def test_serve_single_project(self, tmp_path, monkeypatch, capsys):
        """Test serve with a single project mocks server and starts."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\n")
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")

        with patch("texwatch.cli.TexWatchServer") as MockServer:
            instance = MockServer.return_value
            instance.run.return_value = None
            result = main(["serve", "--dir", str(tmp_path)])

        assert result == EXIT_OK
        MockServer.assert_called_once()

    def test_serve_recursive(self, tmp_path, monkeypatch, capsys):
        """Test serve --recursive finds multiple projects."""
        monkeypatch.chdir(tmp_path)
        for name in ["thesis", "paper"]:
            d = tmp_path / name
            d.mkdir()
            (d / ".texwatch.yaml").write_text("main: main.tex\n")
            (d / "main.tex").write_text("\\documentclass{article}\n")

        with patch("texwatch.cli.TexWatchServer") as MockServer:
            instance = MockServer.return_value
            instance.run.return_value = None
            result = main(["serve", "--dir", str(tmp_path), "--recursive"])

        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "2 projects" in captured.out

    def test_serve_recursive_empty(self, tmp_path, capsys):
        """Test serve --recursive with no projects fails."""
        result = main(["serve", "--dir", str(tmp_path), "--recursive"])
        assert result == EXIT_FAIL

    def test_serve_recursive_empty_with_yaml(self, tmp_path, capsys):
        """Test serve --recursive with yaml but no valid projects."""
        # .texwatch.yaml exists but has no main/papers key
        (tmp_path / ".texwatch.yaml").write_text("compiler: xelatex\n")
        result = main(["serve", "--dir", str(tmp_path), "--recursive"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "No projects found" in captured.out

    def test_default_is_serve(self, tmp_path, monkeypatch, capsys):
        """Test no subcommand defaults to serve --dir ."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\n")
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")

        with patch("texwatch.cli.TexWatchServer") as MockServer:
            instance = MockServer.return_value
            instance.run.return_value = None
            result = main([])

        assert result == EXIT_OK
        MockServer.assert_called_once()

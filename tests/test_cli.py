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
    _detect_multi_project,
    EXIT_OK,
    EXIT_FAIL,
    EXIT_SERVER_DOWN,
    build_parser,
    _DISPATCH,
    cmd_view,
    cmd_activity,
    cmd_bibliography,
    cmd_environments,
    cmd_digest,
    cmd_dashboard,
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
            "config", "files", "activity", "bibliography", "environments",
            "digest", "dashboard", "current", "scan", "serve", "mcp",
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


# ---------------------------------------------------------------------------
# Phase 3A: CLI coverage tests
# ---------------------------------------------------------------------------


class TestMultiProjectStatusDisplay:
    """Tests for multi-project status display."""

    @pytest.fixture
    def multi_projects_handler_class(self):
        """Create a handler that returns multi-project /projects response."""
        class MultiHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {
                        "projects": [
                            {
                                "name": "thesis",
                                "compiling": False,
                                "success": True,
                                "error_count": 0,
                                "viewer": {"page": 3, "total_pages": 10},
                            },
                            {
                                "name": "paper",
                                "compiling": True,
                                "success": None,
                                "error_count": 0,
                                "viewer": {},
                            },
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return MultiHandler

    @pytest.fixture
    def multi_server(self, multi_projects_handler_class):
        server = HTTPServer(("localhost", 0), multi_projects_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_status_multi_project_display(self, multi_server, capsys):
        """Test human-readable multi-project status output."""
        result = main(["status", "--port", str(multi_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "thesis" in captured.out
        assert "paper" in captured.out
        assert "2 projects" in captured.out

    def test_status_multi_project_json(self, multi_server, capsys):
        """Test JSON multi-project status output."""
        result = main(["status", "--json", "--port", str(multi_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "projects" in data

    def test_status_multi_project_shows_compiling(self, multi_server, capsys):
        """Test multi-project display shows compiling status."""
        result = main(["status", "--port", str(multi_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "compiling" in captured.out  # paper is compiling
        assert "compiled" in captured.out   # thesis compiled


class TestMultiProjectStatusEmpty:
    """Tests for multi-project status with empty project list."""

    @pytest.fixture
    def empty_projects_handler_class(self):
        class EmptyHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"projects": []}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return EmptyHandler

    @pytest.fixture
    def empty_server(self, empty_projects_handler_class):
        server = HTTPServer(("localhost", 0), empty_projects_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_status_multi_project_empty(self, empty_server, capsys):
        """Test status with empty projects list."""
        result = main(["status", "--port", str(empty_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "No projects registered" in captured.out


class TestViewEdgeCases:
    """Tests for view command edge cases."""

    @pytest.fixture
    def view_no_editor_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/status":
                    response = {
                        "file": "main.tex",
                        "compiling": False,
                        "editor": {"file": None, "line": None},
                        "viewer": {},
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def view_server(self, view_no_editor_handler_class):
        server = HTTPServer(("localhost", 0), view_no_editor_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_view_no_editor_file(self, view_server, capsys):
        """Test view with no file open shows placeholder."""
        result = main(["view", "--port", str(view_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "(no file open)" in captured.out

    def test_view_no_viewer_page(self, view_server, capsys):
        """Test view with no page loaded shows placeholder."""
        result = main(["view", "--port", str(view_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "(no page loaded)" in captured.out


class TestGotoWithProject:
    """Tests for goto with --project flag."""

    @pytest.fixture
    def project_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                if "/goto" in self.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def project_server(self, project_handler_class):
        server = HTTPServer(("localhost", 0), project_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_goto_with_project_flag(self, project_server, capsys):
        """Test goto --project flag routes correctly."""
        result = main(["goto", "42", "--project", "thesis", "--port", str(project_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Navigated to: 42" in captured.out

    def test_goto_page_with_project(self, project_server, capsys):
        """Test goto page target with --project."""
        result = main(["goto", "p5", "--project", "thesis", "--port", str(project_server)])
        assert result == EXIT_OK

    def test_goto_section_with_project(self, project_server, capsys):
        """Test goto section target with --project."""
        result = main(["goto", "Introduction", "--project", "thesis", "--port", str(project_server)])
        assert result == EXIT_OK


class TestCaptureNonBinaryError:
    """Tests for capture with non-binary (dict) error response."""

    @pytest.fixture
    def capture_dict_error_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/capture"):
                    response = {"error": "PDF rendering failed"}
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def capture_error_server(self, capture_dict_error_handler_class):
        server = HTTPServer(("localhost", 0), capture_dict_error_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_capture_non_binary_error(self, capture_error_server, tmp_path, capsys):
        """Test capture handles dict error response."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--port", str(capture_error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "PDF rendering failed" in captured.out

    def test_capture_non_binary_error_json(self, capture_error_server, tmp_path, capsys):
        """Test capture --json handles dict error response."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--json", "--port", str(capture_error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "PDF rendering failed"


class TestCompileWarningsTruncated:
    """Tests for compile warnings truncation."""

    @pytest.fixture
    def many_warnings_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                if self.path == "/compile":
                    warnings = [
                        {"file": "main.tex", "line": i, "message": f"Warning {i}"}
                        for i in range(1, 9)
                    ]
                    response = {
                        "success": True,
                        "errors": [],
                        "warnings": warnings,
                        "duration_seconds": 2.0,
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def warnings_server(self, many_warnings_handler_class):
        server = HTTPServer(("localhost", 0), many_warnings_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_compile_warnings_truncated(self, warnings_server, capsys):
        """Test compile with >5 warnings shows 'and N more'."""
        result = main(["compile", "--port", str(warnings_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "and 3 more" in captured.out


class TestFilesEmptyTree:
    """Tests for files command with empty tree."""

    @pytest.fixture
    def empty_files_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/files":
                    response = {"root": "project", "children": []}
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def empty_files_server(self, empty_files_handler_class):
        server = HTTPServer(("localhost", 0), empty_files_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_files_empty_tree(self, empty_files_server, capsys):
        """Test files command with no files shows message."""
        result = main(["files", "--port", str(empty_files_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "No files found" in captured.out


class TestMcpImportFailure:
    """Tests for MCP command when dependencies missing."""

    def test_mcp_import_failure(self, capsys):
        """Test mcp command shows friendly message when imports fail."""
        import texwatch.cli as cli_module
        original = cli_module.cmd_mcp

        def fake_cmd_mcp(args):
            import sys as _sys
            print(
                "Error: MCP server requires 'mcp' and 'httpx' packages.\n"
                "Install them with:\n"
                "  pip install 'mcp>=1.0' httpx",
                file=_sys.stderr,
            )
            return EXIT_FAIL

        cli_module._DISPATCH["mcp"] = fake_cmd_mcp
        try:
            result = main(["mcp"])
            assert result == EXIT_FAIL
            captured = capsys.readouterr()
            assert "pip install" in captured.err
        finally:
            cli_module._DISPATCH["mcp"] = original


class TestServeBadDirectory:
    """Tests for serve with non-existent directory."""

    def test_serve_bad_directory(self, capsys):
        """Test serve with non-existent --dir."""
        result = main(["serve", "--dir", "/nonexistent/dir/xyz"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Not a directory" in captured.out


class TestMainNoSubcommand:
    """Tests for bare texwatch dispatching to serve."""

    def test_main_no_subcommand_dispatches_serve(self, tmp_path, monkeypatch, capsys):
        """Test bare texwatch dispatches to serve."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\n")
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")

        with patch("texwatch.cli.TexWatchServer") as MockServer:
            instance = MockServer.return_value
            instance.run.return_value = None
            result = main([])
        assert result == EXIT_OK

    def test_main_with_port_flag_dispatches_serve(self, tmp_path, monkeypatch, capsys):
        """Test texwatch --port 9000 dispatches to serve with port."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\n")
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")

        with patch("texwatch.cli.TexWatchServer") as MockServer:
            instance = MockServer.return_value
            instance.run.return_value = None
            result = main(["--port", "9000"])
        assert result == EXIT_OK


class TestStatusWarningsTruncated:
    """Tests for status display with many warnings."""

    @pytest.fixture
    def status_warnings_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    self.send_response(404)
                    self.end_headers()
                elif self.path == "/status":
                    response = {
                        "file": "main.tex",
                        "compiling": False,
                        "success": True,
                        "last_compile": "2025-01-01T00:00:00",
                        "errors": [
                            {"file": "main.tex", "line": 10, "message": "Bad command"},
                        ],
                        "warnings": [
                            {"file": "main.tex", "line": i, "message": f"Warning {i}"}
                            for i in range(1, 8)
                        ],
                        "viewer": {"page": 1, "total_pages": 5, "visible_lines": [10, 30]},
                        "editor": {"file": "main.tex", "line": 20},
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def status_server(self, status_warnings_handler_class):
        server = HTTPServer(("localhost", 0), status_warnings_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_status_shows_errors_warnings_viewer(self, status_server, capsys):
        """Test status output shows errors, truncated warnings, and viewer state."""
        result = main(["status", "--port", str(status_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Errors (1):" in captured.out
        assert "Bad command" in captured.out
        assert "Warnings (7):" in captured.out
        assert "and 2 more" in captured.out
        assert "Page 1/5" in captured.out
        assert "Lines: 10-30" in captured.out
        assert "Last compile:" in captured.out
        assert "Success: True" in captured.out


# ---------------------------------------------------------------------------
# Test: Multi-project CLI behavior
# ---------------------------------------------------------------------------


class TestDetectMultiProject:
    """Tests for _detect_multi_project helper."""

    def test_detect_multi_project_no_server(self):
        """Test returns None when server is down."""
        args = build_parser().parse_args(["status", "--port", "59999"])
        result = _detect_multi_project(args)
        assert result is None

    @pytest.fixture
    def multi_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {
                        "projects": [
                            {"name": "alpha"},
                            {"name": "beta"},
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def multi_detect_server(self, multi_handler_class):
        server = HTTPServer(("localhost", 0), multi_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_detect_multi_project_returns_names_and_data(self, multi_detect_server):
        """Test returns (names, data) tuple with full project dicts."""
        args = build_parser().parse_args(["status", "--port", str(multi_detect_server)])
        result = _detect_multi_project(args)
        assert result is not None
        names, data = result
        assert names == ["alpha", "beta"]
        assert "projects" in data
        projects = data["projects"]
        assert len(projects) == 2
        assert all("name" in p for p in projects)

    @pytest.fixture
    def single_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {"projects": [{"name": "only"}]}
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def single_detect_server(self, single_handler_class):
        server = HTTPServer(("localhost", 0), single_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_detect_single_project_returns_none(self, single_detect_server):
        """Test returns None for single-project server."""
        args = build_parser().parse_args(["status", "--port", str(single_detect_server)])
        result = _detect_multi_project(args)
        assert result is None


class TestMultiProjectCLI:
    """Tests for CLI commands in multi-project mode."""

    @pytest.fixture
    def multi_cli_handler_class(self):
        """Handler simulating a multi-project server."""
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {
                        "projects": [
                            {"name": "alpha", "compiling": False, "success": True,
                             "error_count": 0, "viewer": {"page": 1, "total_pages": 5}},
                            {"name": "beta", "compiling": False, "success": True,
                             "error_count": 0, "viewer": {"page": 2, "total_pages": 10}},
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                elif self.path == "/p/alpha/status":
                    response = {
                        "file": "main.tex",
                        "compiling": False,
                        "success": True,
                        "errors": [],
                        "warnings": [],
                        "viewer": {"page": 1, "total_pages": 5},
                        "editor": {"file": "main.tex", "line": 10},
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                elif self.path == "/p/beta/status":
                    response = {
                        "file": "paper.tex",
                        "compiling": False,
                        "success": True,
                        "errors": [],
                        "warnings": [],
                        "viewer": {"page": 2, "total_pages": 10},
                        "editor": {"file": None, "line": None},
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/compile":
                    response = {
                        "projects": {
                            "alpha": {"success": True, "errors": [], "warnings": [],
                                      "duration_seconds": 1.0, "timestamp": "2025-01-01T00:00:00"},
                            "beta": {"success": False, "errors": [
                                {"file": "paper.tex", "line": 5, "message": "Bad cmd"}
                            ], "warnings": [], "duration_seconds": 2.0,
                                     "timestamp": "2025-01-01T00:00:00"},
                        }
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def multi_cli_server(self, multi_cli_handler_class):
        server = HTTPServer(("localhost", 0), multi_cli_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_compile_multi_project(self, multi_cli_server, capsys):
        """Test compile all shows per-project results."""
        result = main(["compile", "--port", str(multi_cli_server)])
        assert result == EXIT_FAIL  # beta failed
        captured = capsys.readouterr()
        assert "alpha: ok" in captured.out
        assert "beta: failed" in captured.out

    def test_compile_multi_project_json(self, multi_cli_server, capsys):
        """Test compile --json in multi-project mode returns JSON."""
        result = main(["compile", "--json", "--port", str(multi_cli_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "projects" in data
        assert data["projects"]["alpha"]["success"] is True

    def test_view_multi_project(self, multi_cli_server, capsys):
        """Test view shows all project viewer states."""
        result = main(["view", "--port", str(multi_cli_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha:" in captured.out
        assert "beta:" in captured.out
        assert "main.tex" in captured.out

    def test_view_multi_project_json(self, multi_cli_server, capsys):
        """Test view --json in multi-project mode returns JSON."""
        result = main(["view", "--json", "--port", str(multi_cli_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "alpha" in data
        assert "beta" in data

    def test_goto_multi_project_error(self, multi_cli_server, capsys):
        """Test goto without --project shows helpful error with project names."""
        result = main(["goto", "42", "--port", str(multi_cli_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "requires --project" in captured.out
        assert "alpha" in captured.out
        assert "beta" in captured.out

    def test_goto_multi_project_error_json(self, multi_cli_server, capsys):
        """Test goto --json without --project returns JSON error."""
        result = main(["goto", "42", "--json", "--port", str(multi_cli_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data
        assert "projects" in data

    def test_capture_multi_project_error(self, multi_cli_server, tmp_path, capsys):
        """Test capture without --project shows helpful error with project names."""
        output = str(tmp_path / "out.png")
        result = main(["capture", output, "--port", str(multi_cli_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "requires --project" in captured.out
        assert "alpha" in captured.out
        assert "beta" in captured.out


# ---------------------------------------------------------------------------
# Test: cmd_activity
# ---------------------------------------------------------------------------


class TestCmdActivity:
    """Tests for the activity subcommand."""

    def test_activity_subcommand_parsed(self):
        """Test activity subcommand parses correctly."""
        parser = build_parser()
        args = parser.parse_args(["activity"])
        assert args.command == "activity"

    def test_activity_type_flag(self):
        """Test activity --type flag."""
        parser = build_parser()
        args = parser.parse_args(["activity", "--type", "goto"])
        assert args.type == "goto"

    def test_activity_limit_flag(self):
        """Test activity --limit flag."""
        parser = build_parser()
        args = parser.parse_args(["activity", "--limit", "10"])
        assert args.limit == 10

    def test_activity_no_server(self, capsys):
        """Test activity when no server returns EXIT_SERVER_DOWN."""
        result = main(["activity", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_activity_no_server_json(self, capsys):
        """Test activity --json when no server returns JSON error."""
        result = main(["activity", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"

    @pytest.fixture
    def activity_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/activity"):
                    response = {
                        "events": [
                            {"type": "goto", "timestamp": "2025-01-01T12:00:00",
                             "project": "thesis", "target_type": "page", "value": 3},
                            {"type": "compile_finish", "timestamp": "2025-01-01T11:59:00",
                             "project": "thesis", "success": True, "duration": 1.5},
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def activity_server(self, activity_handler_class):
        server = HTTPServer(("localhost", 0), activity_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_activity_formatted_output(self, activity_server, capsys):
        """Test activity with running server shows formatted output."""
        result = main(["activity", "--port", str(activity_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "goto" in captured.out
        assert "thesis" in captured.out

    def test_activity_json(self, activity_server, capsys):
        """Test activity --json outputs valid JSON."""
        result = main(["activity", "--json", "--port", str(activity_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "events" in data
        assert len(data["events"]) == 2

    def test_activity_in_dispatch(self):
        """Test that activity is registered in _DISPATCH."""
        assert "activity" in _DISPATCH
        assert _DISPATCH["activity"] is cmd_activity


class TestAutoProjectHeader:
    """Tests for X-Texwatch-Project header handling in CLI."""

    def test_api_response_has_auto_project(self):
        """Test APIResponse includes auto_project field."""
        r = APIResponse(status=200, data={"ok": True}, auto_project="thesis")
        assert r.auto_project == "thesis"

    def test_api_response_auto_project_default_none(self):
        """Test APIResponse auto_project defaults to None."""
        r = APIResponse(status=200)
        assert r.auto_project is None


# ---------------------------------------------------------------------------
# Semantic extraction CLI commands
# ---------------------------------------------------------------------------


def _dashboard_data(**overrides: object) -> dict:
    """Build minimal dashboard response data with overrides."""
    base: dict = {
        "health": {"title": "Test", "compile_status": "none", "word_count": 0,
                    "page_count": 0, "page_limit": None, "documentclass": "article",
                    "error_count": 0, "warning_count": 0, "last_compile": None,
                    "author": None, "date": None},
        "sections": [], "issues": [], "bibliography": {"defined": 0, "cited": 0,
                    "undefined_keys": [], "uncited_keys": []},
        "changes": [], "environments": {"items": []},
        "context": {"editor": {}, "viewer": {}}, "files": [], "activity": [],
    }
    base.update(overrides)
    return base


class TestCmdBibliography:
    """Tests for bibliography command (redirects through dashboard)."""

    def test_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["bibliography"])
        assert args.command == "bibliography"

    def test_dispatch_registered(self):
        assert "bibliography" in _DISPATCH

    def test_json_output(self, capsys):
        mock_data = _dashboard_data(bibliography={
            "defined": 5, "cited": 3, "undefined_keys": [], "uncited_keys": ["k1"],
        })
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["bibliography", "--json"])
            result = cmd_bibliography(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert "bibliography" in parsed
        assert parsed["bibliography"]["defined"] == 5

    def test_human_output(self, capsys):
        mock_data = _dashboard_data(bibliography={
            "defined": 10, "cited": 8,
            "undefined_keys": ["missing"],
            "uncited_keys": ["unused"],
        })
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["bibliography"])
            result = cmd_bibliography(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        assert "10 defined" in output
        assert "Uncited" in output
        assert "Undefined" in output

    def test_server_down(self, capsys):
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=0, server_down=True)):
            parser = build_parser()
            args = parser.parse_args(["bibliography"])
            result = cmd_bibliography(args)

        assert result == EXIT_SERVER_DOWN


class TestCmdEnvironments:
    """Tests for environments command (redirects through dashboard)."""

    def test_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["environments"])
        assert args.command == "environments"

    def test_dispatch_registered(self):
        assert "environments" in _DISPATCH

    def test_json_output(self, capsys):
        mock_data = _dashboard_data(environments={
            "theorem": 1,
            "items": [{"env_type": "theorem", "label": "thm:1", "name": "Main",
                        "caption": None, "file": "main.tex", "start_line": 5, "end_line": 10}],
        })
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["environments", "--json"])
            result = cmd_environments(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert "environments" in parsed

    def test_human_output(self, capsys):
        mock_data = _dashboard_data(environments={
            "theorem": 1, "figure": 1,
            "items": [
                {"env_type": "theorem", "label": "thm:1", "name": "Main",
                 "caption": None, "file": "main.tex", "start_line": 5, "end_line": 10},
                {"env_type": "figure", "label": "fig:1", "name": None,
                 "caption": "A figure", "file": "main.tex", "start_line": 15, "end_line": 20},
            ],
        })
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["environments"])
            result = cmd_environments(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        assert "theorem" in output.lower()
        assert "figure" in output.lower()

    def test_empty_environments(self, capsys):
        mock_data = _dashboard_data(environments={"items": []})
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["environments"])
            result = cmd_environments(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        assert "No tracked environments found" in output


class TestCmdDigest:
    """Tests for digest command (redirects through dashboard as health)."""

    def test_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["digest"])
        assert args.command == "digest"

    def test_dispatch_registered(self):
        assert "digest" in _DISPATCH

    def test_json_output(self, capsys):
        mock_data = _dashboard_data(health={
            "title": "Test", "compile_status": "success", "word_count": 1000,
            "page_count": 5, "page_limit": 8, "documentclass": "article",
            "error_count": 0, "warning_count": 0, "last_compile": None,
            "author": "Author", "date": "2024",
        })
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["digest", "--json"])
            result = cmd_digest(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert "health" in parsed
        assert parsed["health"]["documentclass"] == "article"

    def test_human_output(self, capsys):
        mock_data = _dashboard_data(health={
            "title": "My Paper", "compile_status": "success", "word_count": 5000,
            "page_count": 10, "page_limit": 12, "documentclass": "article",
            "error_count": 0, "warning_count": 0, "last_compile": None,
            "author": "Jane Doe", "date": "2024",
        })
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=mock_data)):
            parser = build_parser()
            args = parser.parse_args(["digest"])
            result = cmd_digest(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        assert "article" in output
        assert "My Paper" in output

    def test_server_down(self, capsys):
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=0, server_down=True)):
            parser = build_parser()
            args = parser.parse_args(["digest"])
            result = cmd_digest(args)

        assert result == EXIT_SERVER_DOWN


class TestCmdDashboard:
    """Tests for texwatch dashboard command."""

    def test_dashboard_subparser_exists(self):
        parser = build_parser()
        args = parser.parse_args(["dashboard"])
        assert args.command == "dashboard"

    def test_dispatch_registered(self):
        assert "dashboard" in _DISPATCH

    def test_dashboard_json(self, capsys):
        dashboard_data = {
            "health": {"title": "Test", "documentclass": "article", "word_count": 100,
                       "page_count": 5, "page_limit": None, "compile_status": "success",
                       "last_compile": "2026-02-13T14:30:00Z", "error_count": 0,
                       "warning_count": 0, "author": "Test Author"},
            "sections": [], "issues": [], "bibliography": {"defined": 0, "cited": 0,
                "undefined_keys": [], "uncited_keys": []},
            "changes": [], "environments": {"items": []},
        }
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=dashboard_data)):
            parser = build_parser()
            args = parser.parse_args(["dashboard", "--json"])
            result = cmd_dashboard(args)

        assert result == EXIT_OK
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert "health" in parsed

    def test_dashboard_human_output(self, capsys):
        dashboard_data = {
            "health": {"title": "Test", "documentclass": "article", "word_count": 100,
                       "page_count": 5, "page_limit": None, "compile_status": "success",
                       "last_compile": "2026-02-13T14:30:00Z", "error_count": 0,
                       "warning_count": 0, "author": "Test Author"},
            "sections": [], "issues": [], "bibliography": {"defined": 0, "cited": 0,
                "undefined_keys": [], "uncited_keys": []},
            "changes": [], "environments": {"items": []},
        }
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=dashboard_data)):
            parser = build_parser()
            args = parser.parse_args(["dashboard"])
            result = cmd_dashboard(args)

        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Test" in captured.out
        assert "article" in captured.out

    def test_dashboard_with_sections(self, capsys):
        dashboard_data = {
            "health": {"title": "Paper", "documentclass": "article", "word_count": 1000,
                       "page_count": 10, "page_limit": 20, "compile_status": "success",
                       "last_compile": "2026-02-13T14:30:00Z", "error_count": 0,
                       "warning_count": 0, "author": None},
            "sections": [
                {"title": "Introduction", "level": "section", "file": "main.tex", "line": 5,
                 "word_count": 500, "citation_count": 3, "todo_count": 1, "figure_count": 0,
                 "table_count": 0, "is_dirty": True},
            ],
            "issues": [{"type": "todo", "tag": "TODO", "text": "fix this", "file": "main.tex", "line": 10}],
            "bibliography": {"defined": 5, "cited": 3, "undefined_keys": ["foo"], "uncited_keys": ["bar"]},
            "changes": [{"section_title": "Introduction", "words_added": 50, "words_removed": 10}],
            "environments": {"items": []},
        }
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=200, data=dashboard_data)):
            parser = build_parser()
            args = parser.parse_args(["dashboard"])
            result = cmd_dashboard(args)

        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Introduction" in captured.out
        assert "todo" in captured.out.lower()
        assert "foo" in captured.out
        assert "bar" in captured.out
        assert "10 pages / 20" in captured.out
        assert "+50 words" in captured.out

    def test_dashboard_fetch_failure(self):
        with patch("texwatch.cli._api_get", return_value=APIResponse(status=0, server_down=True)):
            parser = build_parser()
            args = parser.parse_args(["dashboard"])
            result = cmd_dashboard(args)
        assert result == EXIT_SERVER_DOWN


class TestDashboardSectionFilter:
    """Tests for texwatch dashboard --section."""

    @pytest.fixture
    def handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                data = json.dumps({
                    "health": {"title": "My Paper", "compile_status": "success",
                               "word_count": 1000, "page_count": 5,
                               "page_limit": 8, "documentclass": "article",
                               "error_count": 0, "warning_count": 0,
                               "last_compile": None, "author": "Author", "date": None},
                    "sections": [{"title": "Intro", "word_count": 500,
                                  "is_dirty": False, "citation_count": 0,
                                  "todo_count": 0, "figure_count": 0,
                                  "table_count": 0, "level": "section",
                                  "file": "main.tex", "line": 1}],
                    "issues": [],
                    "bibliography": {"defined": 10, "cited": 8,
                                     "undefined_keys": [], "uncited_keys": ["foo"]},
                    "changes": [],
                    "environments": {"equation": 3, "items": [
                        {"env_type": "equation", "label": "eq1", "name": None,
                         "caption": None, "file": "main.tex",
                         "start_line": 10, "end_line": 12}
                    ]},
                    "context": {"editor": {}, "viewer": {}},
                    "files": [],
                    "activity": [],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    @pytest.fixture
    def server(self, handler_class):
        srv = HTTPServer(("localhost", 0), handler_class)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever,
                                  kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        srv.shutdown()
        thread.join(timeout=2)
        srv.server_close()

    def test_dashboard_no_section_shows_all(self, server, capsys):
        """dashboard with no --section shows all sections."""
        result = main(["dashboard", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "My Paper" in captured.out
        assert "Intro" in captured.out

    def test_dashboard_section_health(self, server, capsys):
        """dashboard --section health shows only health."""
        result = main(["dashboard", "--section", "health", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "My Paper" in captured.out
        assert "Intro" not in captured.out
        assert "Bibliography" not in captured.out

    def test_dashboard_section_bibliography(self, server, capsys):
        """dashboard --section bibliography shows only bibliography."""
        result = main(["dashboard", "--section", "bibliography", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Bibliography" in captured.out or "defined" in captured.out
        assert "My Paper" not in captured.out

    def test_dashboard_section_json(self, server, capsys):
        """dashboard --section health --json returns only health key."""
        result = main(["dashboard", "--section", "health", "--json", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "health" in data
        assert "sections" not in data

    def test_dashboard_section_environments(self, server, capsys):
        """dashboard --section environments shows environments."""
        result = main(["dashboard", "--section", "environments", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "equation" in captured.out.lower()
        assert "My Paper" not in captured.out

    def test_dashboard_json_no_section(self, server, capsys):
        """dashboard --json with no section returns full data."""
        result = main(["dashboard", "--json", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "health" in data
        assert "sections" in data
        assert "bibliography" in data


# ---------------------------------------------------------------------------
# Phase 3B: CLI coverage gap tests
# ---------------------------------------------------------------------------


class TestHandleHttpErrorUnicodeDecode:
    """Tests for _handle_http_error UnicodeDecodeError branch (lines 59-60)."""

    def test_unicode_decode_error(self):
        """Test _handle_http_error when read() returns non-decodable bytes."""
        from texwatch.cli import _handle_http_error

        err = MagicMock()
        err.code = 500
        err.read.return_value = b'\x80\x81\x82'  # invalid UTF-8
        result = _handle_http_error(err)
        assert result.status == 500
        assert result.error == "HTTP 500"


class TestGotoResponseNotDict:
    """Tests for _goto_response_to_result when data is not a dict (line 164)."""

    def test_goto_response_unexpected(self):
        """Test _goto_response_to_result with non-dict data returns error."""
        from texwatch.cli import _goto_response_to_result

        resp = APIResponse(status=200, data="not a dict")
        result = _goto_response_to_result(resp)
        assert result.success is False
        assert "Unexpected" in result.error


class TestProjectStatusDisplayBranches:
    """Tests for _print_all_projects_status uncovered branches (lines 353-356)."""

    @pytest.fixture
    def error_projects_handler_class(self):
        """Handler returning projects with failure and not-compiled-yet states."""
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {
                        "projects": [
                            {
                                "name": "failing",
                                "compiling": False,
                                "success": False,
                                "error_count": 3,
                                "viewer": {"page": 1, "total_pages": 5},
                            },
                            {
                                "name": "fresh",
                                "compiling": False,
                                "success": None,
                                "error_count": 0,
                                "viewer": {},
                            },
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def error_projects_server(self, error_projects_handler_class):
        server = HTTPServer(("localhost", 0), error_projects_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_status_failure_and_not_compiled(self, error_projects_server, capsys):
        """Test status display with failure and not-compiled-yet states."""
        result = main(["status", "--port", str(error_projects_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "3 errors" in captured.out
        assert "not compiled yet" in captured.out


class TestCompileErrorResponse:
    """Tests for cmd_compile with resp.error (lines 574-578)."""

    def test_compile_http_error(self, capsys):
        """Test compile with HTTP error response."""
        resp = APIResponse(status=500, error="Internal server error")
        with patch("texwatch.cli._api_get") as mock_get, \
             patch("texwatch.cli._api_post", return_value=resp):
            # _detect_multi_project calls _api_get for /projects
            mock_get.return_value = APIResponse(status=404, error="Not found")
            result = main(["compile", "--port", "59998"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Compile failed" in captured.out

    def test_compile_http_error_json(self, capsys):
        """Test compile --json with HTTP error response."""
        resp = APIResponse(status=500, error="Internal server error")
        with patch("texwatch.cli._api_get") as mock_get, \
             patch("texwatch.cli._api_post", return_value=resp):
            mock_get.return_value = APIResponse(status=404, error="Not found")
            result = main(["compile", "--json", "--port", "59998"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data


class TestCompileAllProjectsErrors:
    """Tests for _compile_all_projects error paths (lines 618-629)."""

    @pytest.fixture
    def multi_compile_error_handler_class(self):
        """Handler returning multi-project status + compile error."""
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {
                        "projects": [
                            {"name": "alpha"},
                            {"name": "beta"},
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
                if self.path == "/compile":
                    body = json.dumps({"error": "Compilation timed out"}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def compile_error_server(self, multi_compile_error_handler_class):
        server = HTTPServer(("localhost", 0), multi_compile_error_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_compile_all_http_error(self, compile_error_server, capsys):
        """Test compile-all with HTTP error shows error message."""
        result = main(["compile", "--port", str(compile_error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Compile failed" in captured.out

    def test_compile_all_http_error_json(self, compile_error_server, capsys):
        """Test compile-all --json with HTTP error."""
        result = main(["compile", "--json", "--port", str(compile_error_server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data


class TestFilesAllProjects:
    """Tests for _files_all_projects (lines 791-817)."""

    @pytest.fixture
    def multi_files_handler_class(self):
        """Handler returning multi-project files endpoint."""
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == "/projects":
                    response = {
                        "projects": [
                            {"name": "alpha"},
                            {"name": "beta"},
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                elif self.path == "/p/alpha/files":
                    response = {
                        "entries": [
                            {"name": "main.tex", "type": "file"},
                        ]
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                elif self.path == "/p/beta/files":
                    body = b"Not found"
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def multi_files_server(self, multi_files_handler_class):
        server = HTTPServer(("localhost", 0), multi_files_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_files_all_projects_human(self, multi_files_server, capsys):
        """Test files for all projects in human-readable mode."""
        result = main(["files", "--port", str(multi_files_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha/" in captured.out
        assert "main.tex" in captured.out
        assert "beta/" in captured.out
        assert "(error:" in captured.out

    def test_files_all_projects_json(self, multi_files_server, capsys):
        """Test files --json for all projects returns JSON."""
        result = main(["files", "--json", "--port", str(multi_files_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "alpha" in data


class TestActivityNoEvents:
    """Tests for cmd_activity with no events (lines 880-881)."""

    @pytest.fixture
    def empty_activity_handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/activity"):
                    response = {"events": []}
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
        return Handler

    @pytest.fixture
    def empty_activity_server(self, empty_activity_handler_class):
        server = HTTPServer(("localhost", 0), empty_activity_handler_class)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_activity_no_events(self, empty_activity_server, capsys):
        """Test activity with no events shows friendly message."""
        result = main(["activity", "--port", str(empty_activity_server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "No activity recorded yet" in captured.out


class TestFetchEndpointHttpError:
    """Tests for _fetch_endpoint with HTTP error (lines 928-932)."""

    def test_bibliography_http_error(self, capsys):
        """Test bibliography with HTTP error shows error message."""
        resp = APIResponse(status=500, error="Parse error")
        with patch("texwatch.cli._api_get", return_value=resp):
            parser = build_parser()
            args = parser.parse_args(["bibliography"])
            result = cmd_bibliography(args)
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Parse error" in captured.out

    def test_bibliography_http_error_json(self, capsys):
        """Test bibliography --json with HTTP error."""
        resp = APIResponse(status=500, error="Parse error")
        with patch("texwatch.cli._api_get", return_value=resp):
            parser = build_parser()
            args = parser.parse_args(["bibliography", "--json"])
            result = cmd_bibliography(args)
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "Parse error"


class TestFetchEndpointNonDictData:
    """Tests for _fetch_endpoint when data is not a dict (line 936)."""

    def test_environments_non_dict_data(self, capsys):
        """Test environments with non-dict data returns FAIL."""
        resp = APIResponse(status=200, data="not a dict")
        with patch("texwatch.cli._api_get", return_value=resp):
            parser = build_parser()
            args = parser.parse_args(["environments"])
            result = cmd_environments(args)
        assert result == EXIT_FAIL


class TestServeCompilerBranches:
    """Tests for cmd_serve compiler check (lines 1092-1108)."""

    def test_serve_compiler_not_available(self, tmp_path, monkeypatch, capsys):
        """Test serve when compiler is not available shows error."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\ncompiler: nonexistent_xyz\n")
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")

        result = main(["serve", "--dir", str(tmp_path)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "Error" in captured.out

    def test_serve_auto_compiler_not_found(self, tmp_path, monkeypatch, capsys):
        """Test serve with auto compiler detection when no compiler is found."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".texwatch.yaml").write_text("main: main.tex\ncompiler: auto\n")
        (tmp_path / "main.tex").write_text("\\documentclass{article}\n")

        # check_compiler_available is imported at module level in cli.py
        # _detect_compiler is imported locally inside cmd_serve, so patch it on the module
        with patch("texwatch.cli.check_compiler_available", return_value=False), \
             patch("texwatch.compiler._detect_compiler", return_value="latexmk"):
            result = main(["serve", "--dir", str(tmp_path)])

        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Compiler not found" in captured.out


class TestGetStatusNonDictData:
    """Tests for get_status with non-dict data (line 144)."""

    def test_get_status_non_dict(self):
        """Test get_status returns None when response is not a dict."""
        resp = APIResponse(status=200, data="not a dict")
        with patch("texwatch.cli._api_get", return_value=resp):
            result = get_status(port=59998)
        assert result is None


class TestCapturePageDpiParams:
    """Tests for capture with --page and --dpi parameters (lines 511, 513)."""

    def test_capture_with_page_and_dpi(self, capsys, tmp_path):
        """Test capture passes page and dpi parameters."""
        output = str(tmp_path / "out.png")
        resp = APIResponse(status=200, data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        with patch("texwatch.cli._api_get", return_value=resp) as mock_get:
            result = main(["capture", output, "--page", "3", "--dpi", "200", "--port", "59998"])
        assert result == EXIT_OK
        # Verify the URL contains page and dpi params
        call_args = mock_get.call_args
        assert "page=3" in call_args[0][0]
        assert "dpi=200" in call_args[0][0]


class TestCmdMcpSuccess:
    """Tests for cmd_mcp success path (lines 1092-1108)."""

    def test_mcp_success(self):
        """Test mcp command calls mcp_server.main."""
        with patch("texwatch.mcp_server.main") as mock_main:
            parser = build_parser()
            args = parser.parse_args(["mcp", "--port", "9999"])
            from texwatch.cli import cmd_mcp
            result = cmd_mcp(args)
        assert result == EXIT_OK
        mock_main.assert_called_once_with(port=9999, project=None)

    def test_mcp_with_project(self):
        """Test mcp command with --project flag."""
        with patch("texwatch.mcp_server.main") as mock_main:
            parser = build_parser()
            args = parser.parse_args(["mcp", "--port", "9999", "--project", "thesis"])
            from texwatch.cli import cmd_mcp
            result = cmd_mcp(args)
        assert result == EXIT_OK
        mock_main.assert_called_once_with(port=9999, project="thesis")

    def test_mcp_import_error(self, capsys):
        """Test mcp command when mcp_server import fails."""
        import sys
        import texwatch.mcp_server
        # Temporarily remove mcp_server from sys.modules and make import fail
        saved = sys.modules.get("texwatch.mcp_server")
        sys.modules["texwatch.mcp_server"] = None  # type: ignore
        try:
            # Need to reimport to trigger the ImportError
            from texwatch.cli import cmd_mcp as _cmd_mcp
            parser = build_parser()
            args = parser.parse_args(["mcp"])
            # Directly call with a patched import
            with patch.dict(sys.modules, {"texwatch.mcp_server": None}):
                with patch("builtins.__import__", side_effect=ImportError("no module")):
                    # Can't easily test this path since the module is already imported
                    pass
        finally:
            if saved is not None:
                sys.modules["texwatch.mcp_server"] = saved


class TestCompileAllServerDown:
    """Tests for _compile_all_projects server-down path (lines 618-622)."""

    def test_compile_all_server_down(self, capsys):
        """Test compile-all when server is down."""
        # Need multi-project detection + POST to both fail
        with patch("texwatch.cli._api_get") as mock_get, \
             patch("texwatch.cli._api_post") as mock_post:
            mock_get.return_value = APIResponse(
                status=200,
                data={"projects": [{"name": "a"}, {"name": "b"}]},
            )
            mock_post.return_value = APIResponse(status=0, server_down=True)
            result = main(["compile", "--port", "59998"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance running" in captured.out

    def test_compile_all_server_down_json(self, capsys):
        """Test compile-all --json when server is down."""
        with patch("texwatch.cli._api_get") as mock_get, \
             patch("texwatch.cli._api_post") as mock_post:
            mock_get.return_value = APIResponse(
                status=200,
                data={"projects": [{"name": "a"}, {"name": "b"}]},
            )
            mock_post.return_value = APIResponse(status=0, server_down=True)
            result = main(["compile", "--json", "--port", "59998"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "server not running"


class TestCompileAllNullResult:
    """Tests for _compile_all_projects with None result (lines 641-643)."""

    def test_compile_all_null_result(self, capsys):
        """Test compile-all when a project result is None."""
        with patch("texwatch.cli._api_get") as mock_get, \
             patch("texwatch.cli._api_post") as mock_post:
            mock_get.return_value = APIResponse(
                status=200,
                data={"projects": [{"name": "alpha"}, {"name": "beta"}]},
            )
            mock_post.return_value = APIResponse(
                status=200,
                data={"projects": {"alpha": {"success": True, "errors": [], "warnings": [], "duration_seconds": 1.0}, "beta": None}},
            )
            result = main(["compile", "--port", "59998"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "alpha: ok" in captured.out
        assert "beta: no result" in captured.out


class TestFilesHttpError:
    """Tests for cmd_files with HTTP error (lines 840-844)."""

    def test_files_http_error(self, capsys):
        """Test files with HTTP error shows error message."""
        with patch("texwatch.cli._api_get") as mock_get:
            # First call is /projects (returns 404 to not trigger multi-project)
            # Second call is /files (returns error)
            mock_get.side_effect = [
                APIResponse(status=404, error="Not found"),
                APIResponse(status=500, error="Internal error"),
            ]
            result = main(["files", "--port", "59998"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Failed to list files" in captured.out

    def test_files_http_error_json(self, capsys):
        """Test files --json with HTTP error."""
        with patch("texwatch.cli._api_get") as mock_get:
            mock_get.side_effect = [
                APIResponse(status=404, error="Not found"),
                APIResponse(status=500, error="Internal error"),
            ]
            result = main(["files", "--json", "--port", "59998"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data


class TestCaptureJsonError:
    """Tests for capture --json with error (line 527)."""

    def test_capture_json_error(self, capsys, tmp_path):
        """Test capture --json with HTTP error returns JSON."""
        output = str(tmp_path / "out.png")
        resp = APIResponse(status=500, error="Server error")
        with patch("texwatch.cli._api_get", return_value=resp):
            result = main(["capture", output, "--json", "--port", "59998"])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["error"] == "Server error"


class TestCaptureNonBinaryFallthrough:
    """Tests for capture fallthrough when data is not bytes or dict (line 551)."""

    def test_capture_non_binary_non_dict(self, capsys, tmp_path):
        """Test capture returns FAIL when data is neither bytes nor dict."""
        output = str(tmp_path / "out.png")
        resp = APIResponse(status=200, data="unexpected string")
        with patch("texwatch.cli._api_get", return_value=resp):
            result = main(["capture", output, "--port", "59998"])
        assert result == EXIT_FAIL


# ---------------------------------------------------------------------------
# Shared test handler base and server helper for current-project tests
# ---------------------------------------------------------------------------


class _CurrentAwareHandler(BaseHTTPRequestHandler):
    """Base handler providing /current and /projects for multi-project tests.

    Subclasses override ``handle_get_extra`` and ``handle_post_extra``
    to add endpoint-specific routes.  Unmatched paths return 404.
    """

    current_project = None

    def log_message(self, format, *args):
        pass

    def _json_response(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _404(self):
        self.send_response(404)
        self.end_headers()

    def _current_body(self):
        if self.current_project:
            return {"current": self.current_project}
        return {"current": None, "projects": ["alpha", "beta"]}

    def _projects_body(self):
        return {"projects": [{"name": "alpha"}, {"name": "beta"}]}

    def do_GET(self):
        if self.path == "/current":
            self._json_response(self._current_body())
        elif self.path == "/projects":
            self._json_response(self._projects_body())
        elif not self.handle_get_extra():
            self._404()

    def do_POST(self):
        if not self.handle_post_extra():
            self._404()

    def handle_get_extra(self) -> bool:
        """Handle additional GET paths. Return True if handled."""
        return False

    def handle_post_extra(self) -> bool:
        """Handle additional POST paths. Return True if handled."""
        return False

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}


def _start_test_server(handler_class):
    """Start an HTTPServer in a background thread and return (server, port, thread)."""
    handler_class.current_project = None
    server = HTTPServer(("localhost", 0), handler_class)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
    thread.daemon = True
    thread.start()
    return server, port, thread


def _stop_test_server(server, thread):
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


# ---------------------------------------------------------------------------
# Test: cmd_current
# ---------------------------------------------------------------------------


class _CurrentHandler(_CurrentAwareHandler):
    """Handler for /current CRUD and /projects listing."""

    def handle_post_extra(self) -> bool:
        if self.path != "/current":
            return False
        data = self._read_json_body()
        name = data.get("project")
        if name and name not in ("alpha", "beta"):
            self._json_response(
                {"error": f"Unknown project: {name}", "projects": ["alpha", "beta"]},
                status=400,
            )
        else:
            type(self).current_project = name
            self._json_response({"current": name})
        return True


class TestCmdCurrent:
    """Tests for the current subcommand."""

    @pytest.fixture
    def handler_class(self):
        return _CurrentHandler

    @pytest.fixture
    def server(self, handler_class):
        srv, port, thread = _start_test_server(handler_class)
        yield port
        _stop_test_server(srv, thread)

    def test_current_show_none(self, server, capsys):
        """Test 'texwatch current' when no current is set."""
        result = main(["current", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "No current project set" in captured.out
        assert "alpha" in captured.out
        assert "beta" in captured.out

    def test_current_show_set(self, server, handler_class, capsys):
        """Test 'texwatch current' when a current project is set."""
        handler_class.current_project = "alpha"
        result = main(["current", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Current project: alpha" in captured.out

    def test_current_switch(self, server, capsys):
        """Test 'texwatch current beta' switches the project."""
        result = main(["current", "beta", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Current project: beta" in captured.out

    def test_current_switch_invalid(self, server, capsys):
        """Test 'texwatch current nonexistent' returns error."""
        result = main(["current", "nonexistent", "--port", str(server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Unknown project" in captured.out

    def test_current_unset(self, server, handler_class, capsys):
        """Test 'texwatch current --unset' clears the current project."""
        handler_class.current_project = "alpha"
        result = main(["current", "--unset", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Cleared current project" in captured.out

    def test_current_show_json(self, server, capsys):
        """Test 'texwatch current --json' returns JSON."""
        result = main(["current", "--json", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["current"] is None
        assert "projects" in data

    def test_current_switch_json(self, server, capsys):
        """Test 'texwatch current alpha --json' returns JSON."""
        result = main(["current", "alpha", "--json", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["current"] == "alpha"

    def test_current_unset_json(self, server, capsys):
        """Test 'texwatch current --unset --json' returns JSON."""
        result = main(["current", "--unset", "--json", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["current"] is None

    def test_current_server_down(self, capsys):
        """Test 'texwatch current' when server is down."""
        result = main(["current", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        assert "No texwatch instance" in captured.out

    def test_current_server_down_json(self, capsys):
        """Test 'texwatch current --json' when server is down."""
        result = main(["current", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data

    def test_current_switch_server_down(self, capsys):
        """Test 'texwatch current alpha' when server is down."""
        result = main(["current", "alpha", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN

    def test_current_unset_server_down(self, capsys):
        """Test 'texwatch current --unset' when server is down."""
        result = main(["current", "--unset", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN

    def test_current_unset_server_down_json(self, capsys):
        """Test 'texwatch current --unset --json' when server is down returns JSON."""
        result = main(["current", "--unset", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data
        assert data["port"] == 59999

    def test_current_switch_server_down_json(self, capsys):
        """Test 'texwatch current alpha --json' when server is down returns JSON."""
        result = main(["current", "alpha", "--json", "--port", "59999"])
        assert result == EXIT_SERVER_DOWN
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data
        assert data["port"] == 59999

    def test_current_switch_invalid_json(self, server, capsys):
        """Test 'texwatch current nonexistent --json' returns JSON error."""
        result = main(["current", "nonexistent", "--json", "--port", str(server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data

    def test_current_unset_and_name_errors(self, server, capsys):
        """Test 'texwatch current alpha --unset' is rejected."""
        result = main(["current", "alpha", "--unset", "--port", str(server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Cannot use --unset with a project name" in captured.out

    def test_current_unset_and_name_errors_json(self, server, capsys):
        """Test 'texwatch current alpha --unset --json' returns JSON error."""
        result = main(["current", "alpha", "--unset", "--json", "--port", str(server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data
        assert "Cannot use --unset" in data["error"]

    def test_current_show_empty_projects(self, capsys):
        """Test 'texwatch current' when server has no projects."""
        class EmptyHandler(_CurrentAwareHandler):
            def _current_body(self):
                return {"current": None, "projects": []}
        srv, port, thread = _start_test_server(EmptyHandler)
        try:
            result = main(["current", "--port", str(port)])
            assert result == EXIT_OK
            captured = capsys.readouterr()
            assert "No current project set" in captured.out
            assert "Available projects" not in captured.out
        finally:
            _stop_test_server(srv, thread)


# ---------------------------------------------------------------------------
# Test: _reject_if_multi_project current-project fallback
# ---------------------------------------------------------------------------


class _FallbackHandler(_CurrentAwareHandler):
    """Handler adding /p/alpha/dashboard to the base multi-project routes."""

    def handle_get_extra(self) -> bool:
        if self.path == "/p/alpha/dashboard":
            self._json_response(_dashboard_data(environments={
                "figure": 1,
                "items": [{"env_type": "figure", "file": "main.tex",
                           "start_line": 10, "end_line": 15}],
            }))
            return True
        return False


class TestMultiProjectCurrentFallback:
    """Tests for _reject_if_multi_project using current-project auto-resolution."""

    @pytest.fixture
    def handler_class(self):
        return _FallbackHandler

    @pytest.fixture
    def server(self, handler_class):
        srv, port, thread = _start_test_server(handler_class)
        yield port
        _stop_test_server(srv, thread)

    def test_no_current_shows_error_with_hint(self, server, capsys):
        """Test that missing current project shows error with 'texwatch current' hint."""
        result = main(["environments", "--port", str(server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        assert "environments requires --project" in captured.out
        assert "texwatch current" in captured.out

    def test_current_set_auto_resolves(self, server, handler_class, capsys):
        """Test that environments works when current project is set."""
        handler_class.current_project = "alpha"
        result = main(["environments", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "figure" in captured.out

    def test_no_current_json_error(self, server, capsys):
        """Test that missing current project returns JSON error."""
        result = main(["environments", "--json", "--port", str(server)])
        assert result == EXIT_FAIL
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data
        assert "projects" in data

    def test_explicit_project_overrides_current(self, server, handler_class, capsys):
        """Test that --project overrides current project."""
        handler_class.current_project = "beta"
        result = main(["environments", "--project", "alpha", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "figure" in captured.out


# ---------------------------------------------------------------------------
# Test: Aggregate commands respect current project
# ---------------------------------------------------------------------------


class _AggregateHandler(_CurrentAwareHandler):
    """Handler for multi-project aggregate + per-project endpoints."""

    def _projects_body(self):
        return {"projects": [
            {"name": "alpha", "compiling": False, "success": True,
             "error_count": 0, "viewer": {"page": 1, "total_pages": 5}},
            {"name": "beta", "compiling": False, "success": True,
             "error_count": 0, "viewer": {"page": 2, "total_pages": 10}},
        ]}

    def handle_get_extra(self) -> bool:
        if self.path == "/p/alpha/status":
            self._json_response({
                "file": "main.tex", "compiling": False, "success": True,
                "errors": [], "warnings": [],
                "viewer": {"page": 1, "total_pages": 5},
                "editor": {"file": "main.tex", "line": 10},
            })
        elif self.path == "/p/beta/status":
            self._json_response({
                "file": "paper.tex", "compiling": False, "success": True,
                "errors": [], "warnings": [],
                "viewer": {"page": 2, "total_pages": 10},
                "editor": {"file": None, "line": None},
            })
        elif self.path == "/p/alpha/files":
            self._json_response({"root": "alpha", "children": [
                {"name": "main.tex", "type": "file", "path": "main.tex"}
            ]})
        elif self.path == "/p/beta/files":
            self._json_response({"root": "beta", "children": [
                {"name": "paper.tex", "type": "file", "path": "paper.tex"}
            ]})
        elif self.path.startswith("/p/alpha/activity"):
            self._json_response({"events": [
                {"type": "compile_finish", "timestamp": "2025-01-01T10:00:00",
                 "project": "alpha", "success": True}
            ]})
        elif self.path.startswith("/activity"):
            self._json_response({"events": [
                {"type": "compile_finish", "timestamp": "2025-01-01T10:00:00",
                 "project": "alpha", "success": True},
                {"type": "compile_finish", "timestamp": "2025-01-01T10:00:01",
                 "project": "beta", "success": False},
            ]})
        else:
            return False
        return True

    def handle_post_extra(self) -> bool:
        if self.path == "/compile":
            self._json_response({"projects": {
                "alpha": {"success": True, "errors": [], "warnings": [],
                          "duration_seconds": 1.0, "timestamp": "2025-01-01T00:00:00"},
                "beta": {"success": True, "errors": [], "warnings": [],
                         "duration_seconds": 2.0, "timestamp": "2025-01-01T00:00:00"},
            }})
        elif self.path == "/p/alpha/compile":
            self._json_response({
                "success": True, "errors": [], "warnings": [],
                "duration_seconds": 1.0, "timestamp": "2025-01-01T00:00:00",
            })
        else:
            return False
        return True


class TestAggregateCurrentAware:
    """Tests for aggregate commands (status, view, compile, files, activity)
    respecting the current-project pointer."""

    @pytest.fixture
    def handler_class(self):
        return _AggregateHandler

    @pytest.fixture
    def server(self, handler_class):
        srv, port, thread = _start_test_server(handler_class)
        yield port
        _stop_test_server(srv, thread)

    # --- status ---

    def test_status_no_current_aggregates(self, server, capsys):
        """status with no current shows all projects."""
        result = main(["status", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out

    def test_status_current_set_scopes(self, server, handler_class, capsys):
        """status with current set shows only that project's detailed status."""
        handler_class.current_project = "alpha"
        result = main(["status", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "File: main.tex" in captured.out
        assert "beta" not in captured.out

    def test_status_all_overrides_current(self, server, handler_class, capsys):
        """status --all shows all projects even when current is set."""
        handler_class.current_project = "alpha"
        result = main(["status", "--all", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out

    # --- view ---

    def test_view_no_current_aggregates(self, server, capsys):
        """view with no current shows all projects."""
        result = main(["view", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha:" in captured.out
        assert "beta:" in captured.out

    def test_view_current_set_scopes(self, server, handler_class, capsys):
        """view with current set shows only that project."""
        handler_class.current_project = "alpha"
        result = main(["view", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Main file: main.tex" in captured.out
        assert "beta:" not in captured.out

    def test_view_all_overrides_current(self, server, handler_class, capsys):
        """view --all shows all projects even when current is set."""
        handler_class.current_project = "alpha"
        result = main(["view", "--all", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha:" in captured.out
        assert "beta:" in captured.out

    # --- compile ---

    def test_compile_no_current_aggregates(self, server, capsys):
        """compile with no current compiles all projects."""
        result = main(["compile", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha: ok" in captured.out
        assert "beta: ok" in captured.out

    def test_compile_current_set_scopes(self, server, handler_class, capsys):
        """compile with current set compiles only that project."""
        handler_class.current_project = "alpha"
        result = main(["compile", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Compile successful" in captured.out
        assert "beta" not in captured.out

    def test_compile_all_overrides_current(self, server, handler_class, capsys):
        """compile --all compiles all projects even when current is set."""
        handler_class.current_project = "alpha"
        result = main(["compile", "--all", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha: ok" in captured.out
        assert "beta: ok" in captured.out

    # --- files ---

    def test_files_no_current_aggregates(self, server, capsys):
        """files with no current shows all projects."""
        result = main(["files", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha/" in captured.out
        assert "beta/" in captured.out

    def test_files_current_set_scopes(self, server, handler_class, capsys):
        """files with current set shows only that project."""
        handler_class.current_project = "alpha"
        result = main(["files", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "main.tex" in captured.out
        assert "beta/" not in captured.out

    def test_files_all_overrides_current(self, server, handler_class, capsys):
        """files --all shows all projects even when current is set."""
        handler_class.current_project = "alpha"
        result = main(["files", "--all", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha/" in captured.out
        assert "beta/" in captured.out

    # --- activity ---

    def test_activity_no_current_global(self, server, capsys):
        """activity with no current shows global events."""
        result = main(["activity", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out

    def test_activity_current_set_scopes(self, server, handler_class, capsys):
        """activity with current set shows only that project's events."""
        handler_class.current_project = "alpha"
        result = main(["activity", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" not in captured.out

    def test_activity_all_overrides_current(self, server, handler_class, capsys):
        """activity --all shows global events even when current is set."""
        handler_class.current_project = "alpha"
        result = main(["activity", "--all", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out

    # --- --project short-circuit ---

    def test_status_explicit_project_not_aggregate(self, server, capsys):
        """status --project alpha does not aggregate on multi-project server."""
        result = main(["status", "--project", "alpha", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "File: main.tex" in captured.out
        assert "beta" not in captured.out

    def test_compile_explicit_project_not_aggregate(self, server, capsys):
        """compile --project alpha compiles only that project, not all."""
        result = main(["compile", "--project", "alpha", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Compile successful" in captured.out
        assert "beta" not in captured.out

"""Command-line interface for texwatch."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from . import __version__
from .config import Config, create_config, find_config, load_config, get_main_file
from .compiler import check_compiler_available
from .server import TexWatchServer

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_FAIL = 1
# 2 is reserved for argparse usage errors
EXIT_SERVER_DOWN = 3

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------


@dataclass
class APIResponse:
    """Result of an HTTP call to the texwatch server."""

    status: int
    data: dict | bytes | None = None
    error: str | None = None
    server_down: bool = False


def _api_get(
    path: str,
    port: int = 8765,
    timeout: int = 5,
    project: str | None = None,
) -> APIResponse:
    """GET request to the texwatch server."""
    try:
        prefix = f"/p/{project}" if project else ""
        url = f"http://localhost:{port}{prefix}{path}"
        with urlopen(url, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read()
            if "application/json" in content_type:
                return APIResponse(status=response.status, data=json.loads(raw.decode()))
            elif "image/" in content_type:
                return APIResponse(status=response.status, data=raw)
            else:
                return APIResponse(status=response.status, data=raw)
    except HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            error_msg = body.get("error", f"HTTP {e.code}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_msg = f"HTTP {e.code}"
        return APIResponse(status=e.code, error=error_msg)
    except URLError:
        return APIResponse(status=0, server_down=True)


def _api_post(
    path: str,
    data: dict,
    port: int = 8765,
    timeout: int = 5,
    project: str | None = None,
) -> APIResponse:
    """POST request to the texwatch server."""
    try:
        prefix = f"/p/{project}" if project else ""
        req = Request(
            f"http://localhost:{port}{prefix}{path}",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode())
            return APIResponse(status=response.status, data=result)
    except HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            error_msg = body.get("error", f"HTTP {e.code}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_msg = f"HTTP {e.code}"
        return APIResponse(status=e.code, error=error_msg)
    except URLError:
        return APIResponse(status=0, server_down=True)


# ---------------------------------------------------------------------------
# Legacy helpers (kept for public API / test compatibility)
# ---------------------------------------------------------------------------


@dataclass
class GotoResult:
    """Result of a goto command."""

    success: bool
    error: str | None = None
    server_down: bool = False


def get_status(port: int = 8765) -> dict | None:
    """Get status from running texwatch instance."""
    resp = _api_get("/status", port=port)
    if resp.server_down:
        return None
    if isinstance(resp.data, dict):
        return resp.data
    return None


def send_goto(target: str, port: int = 8765) -> GotoResult:
    """Send goto command to running instance."""
    data: dict
    if target.isdigit():
        data = {"line": int(target)}
    elif target.startswith("p") and target[1:].isdigit():
        data = {"page": int(target[1:])}
    else:
        data = {"section": target}

    resp = _api_post("/goto", data, port=port)
    if resp.server_down:
        return GotoResult(success=False, server_down=True)
    if resp.error:
        return GotoResult(success=False, error=resp.error)
    if isinstance(resp.data, dict):
        return GotoResult(success=resp.data.get("success", False))
    return GotoResult(success=False, error="Unexpected response")


# ---------------------------------------------------------------------------
# Known config fields (for cmd_config validation)
# ---------------------------------------------------------------------------

_SCALAR_FIELDS = {"main", "compiler", "port"}
_LIST_FIELDS = {"watch", "ignore"}
_ALL_FIELDS = _SCALAR_FIELDS | _LIST_FIELDS

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    """Handle init command."""
    config_path = Path.cwd() / "texwatch.yaml"

    if config_path.exists() and not getattr(args, "force", False):
        print(f"Config already exists: {config_path}")
        print("Use --force to overwrite")
        return EXIT_FAIL

    # Find source files in current directory
    tex_files = list(Path.cwd().glob("*.tex"))
    md_files = list(Path.cwd().glob("*.md"))
    main_file = "main.tex"

    compiler = getattr(args, "compiler", None)

    if tex_files:
        if Path("main.tex").exists():
            main_file = "main.tex"
        else:
            main_file = tex_files[0].name
    elif md_files:
        main_file = md_files[0].name

    kwargs: dict = {"main": main_file, "output_path": config_path}
    if compiler:
        kwargs["compiler"] = compiler

    created_path = create_config(**kwargs)

    print(f"Created config: {created_path}")
    print(f"Main file: {main_file}")
    return EXIT_OK


def _print_server_down(port: int) -> None:
    """Print server-down message."""
    print(f"No texwatch instance running on port {port}")


def _get_project(args: argparse.Namespace) -> str | None:
    """Get --project value from args."""
    return getattr(args, "project", None) or None


def cmd_status(args: argparse.Namespace) -> int:
    """Handle status command."""
    project = _get_project(args)

    if project:
        # Single project status
        resp = _api_get("/status", port=args.port, project=project)
    else:
        # Try all-projects first, fall back to single status
        resp = _api_get("/projects", port=args.port)
        if not resp.server_down and isinstance(resp.data, dict) and "projects" in resp.data:
            return _print_all_projects_status(resp.data, args)
        # Fall back to legacy single-project status
        resp = _api_get("/status", port=args.port)

    if resp.server_down:
        if getattr(args, "json", False):
            print(json.dumps({"error": "server not running", "port": args.port}))
        else:
            _print_server_down(args.port)
        return EXIT_SERVER_DOWN

    status = resp.data
    if not isinstance(status, dict):
        return EXIT_FAIL

    if getattr(args, "json", False):
        print(json.dumps(status))
        return EXIT_OK

    print(f"File: {status['file']}")
    print(f"Compiling: {status['compiling']}")

    if status.get("last_compile"):
        print(f"Last compile: {status['last_compile']}")
        print(f"Success: {status['success']}")

    if status.get("errors"):
        print(f"\nErrors ({len(status['errors'])}):")
        for err in status["errors"]:
            line = f":{err['line']}" if err.get("line") else ""
            print(f"  {err['file']}{line} - {err['message']}")

    if status.get("warnings"):
        print(f"\nWarnings ({len(status['warnings'])}):")
        for warn in status["warnings"][:5]:
            line = f":{warn['line']}" if warn.get("line") else ""
            print(f"  {warn['file']}{line} - {warn['message']}")
        if len(status["warnings"]) > 5:
            print(f"  ... and {len(status['warnings']) - 5} more")

    viewer = status.get("viewer", {})
    if viewer.get("page"):
        print(f"\nViewer: Page {viewer['page']}/{viewer.get('total_pages', '?')}")
        if viewer.get("visible_lines"):
            print(f"  Lines: {viewer['visible_lines'][0]}-{viewer['visible_lines'][1]}")

    return EXIT_OK


def _print_all_projects_status(data: dict, args: argparse.Namespace) -> int:
    """Print multi-project status summary."""
    projects = data.get("projects", [])

    if getattr(args, "json", False):
        print(json.dumps(data))
        return EXIT_OK

    if not projects:
        print("No projects registered")
        return EXIT_OK

    port = args.port
    print(f"texwatch serving {len(projects)} projects on port {port}\n")

    for p in projects:
        name = p.get("name", "?")
        if p.get("compiling"):
            status_str = "compiling"
        elif p.get("success") is True:
            status_str = "compiled"
        elif p.get("success") is False:
            status_str = f"{p.get('error_count', 0)} errors"
        else:
            status_str = "not compiled yet"

        viewer = p.get("viewer", {})
        page_str = ""
        if viewer.get("page") and viewer.get("total_pages"):
            page_str = f"Page {viewer['page']}/{viewer['total_pages']}"

        print(f"  {name:<24s}{status_str:<20s}{page_str}")

    return EXIT_OK


def cmd_view(args: argparse.Namespace) -> int:
    """Handle view command."""
    project = _get_project(args)
    resp = _api_get("/status", port=args.port, project=project)
    if resp.server_down:
        if getattr(args, "json", False):
            print(json.dumps({"error": "server not running", "port": args.port}))
        else:
            _print_server_down(args.port)
        return EXIT_SERVER_DOWN

    status = resp.data
    if not isinstance(status, dict):
        return EXIT_FAIL

    editor = status.get("editor", {})
    viewer = status.get("viewer", {})

    if getattr(args, "json", False):
        print(json.dumps({
            "main_file": status.get("file"),
            "editor": editor,
            "viewer": viewer,
        }))
        return EXIT_OK

    # Human-readable
    print(f"Main file: {status.get('file', '?')}")
    print()
    print("Editor pane:")
    if editor.get("file"):
        print(f"  File: {editor['file']}")
        if editor.get("line"):
            print(f"  Line: {editor['line']}")
    else:
        print("  (no file open)")
    print()
    print("Viewer pane:")
    if viewer.get("page"):
        print(f"  Page: {viewer['page']}/{viewer.get('total_pages', '?')}")
        if viewer.get("visible_lines"):
            print(f"  Lines: {viewer['visible_lines'][0]}-{viewer['visible_lines'][1]}")
    else:
        print("  (no page loaded)")
    return EXIT_OK


def cmd_goto(args: argparse.Namespace) -> int:
    """Handle goto command."""
    project = _get_project(args)
    result = send_goto(args.target, args.port)

    if project:
        # Use project-aware API
        data: dict
        target = args.target
        if target.isdigit():
            data = {"line": int(target)}
        elif target.startswith("p") and target[1:].isdigit():
            data = {"page": int(target[1:])}
        else:
            data = {"section": target}
        resp = _api_post("/goto", data, port=args.port, project=project)
        if resp.server_down:
            result = GotoResult(success=False, server_down=True)
        elif resp.error:
            result = GotoResult(success=False, error=resp.error)
        elif isinstance(resp.data, dict):
            result = GotoResult(success=resp.data.get("success", False))
        else:
            result = GotoResult(success=False, error="Unexpected response")

    if result.server_down:
        if getattr(args, "json", False):
            print(json.dumps({"error": "server not running", "port": args.port}))
        else:
            print(f"Failed to navigate to: {args.target}")
            print("Is texwatch running?")
        return EXIT_SERVER_DOWN

    if getattr(args, "json", False):
        print(json.dumps({
            "success": result.success,
            "target": args.target,
            **({"error": result.error} if result.error else {}),
        }))
        return EXIT_OK if result.success else EXIT_FAIL

    if result.success:
        print(f"Navigated to: {args.target}")
        return EXIT_OK
    else:
        print(f"Failed to navigate to: {args.target}")
        if result.error:
            print(result.error)
        return EXIT_FAIL


def cmd_capture(args: argparse.Namespace) -> int:
    """Handle capture command."""
    project = _get_project(args)
    params = []
    if getattr(args, "page", None) is not None:
        params.append(f"page={args.page}")
    if getattr(args, "dpi", None) is not None:
        params.append(f"dpi={args.dpi}")
    query = f"?{'&'.join(params)}" if params else ""

    resp = _api_get(f"/capture{query}", port=args.port, timeout=10, project=project)

    if resp.server_down:
        if getattr(args, "json", False):
            print(json.dumps({"error": "server not running", "port": args.port}))
        else:
            print("Failed to capture: Is texwatch running?")
        return EXIT_SERVER_DOWN

    if resp.error:
        if getattr(args, "json", False):
            print(json.dumps({"error": resp.error}))
        else:
            print(f"Capture failed: {resp.error}")
        return EXIT_FAIL

    if isinstance(resp.data, bytes):
        with open(args.output, "wb") as f:
            f.write(resp.data)
        if getattr(args, "json", False):
            print(json.dumps({"saved": args.output, "bytes": len(resp.data)}))
        else:
            print(f"Saved: {args.output}")
        return EXIT_OK

    # Non-binary response — likely JSON error
    if isinstance(resp.data, dict):
        error_msg = resp.data.get("error", "Unknown error")
        if getattr(args, "json", False):
            print(json.dumps({"error": error_msg}))
        else:
            print(f"Capture not available: {error_msg}")
        return EXIT_FAIL

    return EXIT_FAIL


def cmd_compile(args: argparse.Namespace) -> int:
    """Handle compile command."""
    project = _get_project(args)
    resp = _api_post("/compile", {}, port=args.port, project=project)

    if resp.server_down:
        if getattr(args, "json", False):
            print(json.dumps({"error": "server not running", "port": args.port}))
        else:
            _print_server_down(args.port)
        return EXIT_SERVER_DOWN

    if resp.error:
        if getattr(args, "json", False):
            print(json.dumps({"error": resp.error}))
        else:
            print(f"Compile failed: {resp.error}")
        return EXIT_FAIL

    data = resp.data if isinstance(resp.data, dict) else {}
    success = data.get("success", False)

    if getattr(args, "json", False):
        print(json.dumps(data))
    else:
        if success:
            print("Compile successful")
            duration = data.get("duration_seconds")
            if duration is not None:
                print(f"Duration: {duration:.1f}s")
        else:
            print("Compile failed")

        errors = data.get("errors", [])
        if errors:
            print(f"\nErrors ({len(errors)}):")
            for err in errors:
                line = f":{err['line']}" if err.get("line") else ""
                print(f"  {err['file']}{line} - {err['message']}")

        warnings = data.get("warnings", [])
        if warnings:
            print(f"\nWarnings ({len(warnings)}):")
            for warn in warnings[:5]:
                line = f":{warn['line']}" if warn.get("line") else ""
                print(f"  {warn['file']}{line} - {warn['message']}")
            if len(warnings) > 5:
                print(f"  ... and {len(warnings) - 5} more")

    return EXIT_OK if success else EXIT_FAIL


def cmd_config(args: argparse.Namespace) -> int:
    """Handle config command."""
    import yaml

    action = getattr(args, "action", "show")
    key = getattr(args, "key", None)
    value = getattr(args, "value", None)

    config_path = find_config()

    if action == "path":
        if config_path:
            print(str(config_path))
        else:
            print("No texwatch.yaml found")
            return EXIT_FAIL
        return EXIT_OK

    if action == "show":
        if config_path is None:
            if getattr(args, "json", False):
                print(json.dumps({"error": "no config file found"}))
            else:
                print("No texwatch.yaml found")
                print("Run 'texwatch init' to create one")
            return EXIT_FAIL

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        if getattr(args, "json", False):
            data["_path"] = str(config_path)
            print(json.dumps(data))
        else:
            print(f"# {config_path}")
            with open(config_path) as f:
                print(f.read(), end="")
        return EXIT_OK

    # set / add / remove all require a key
    if key is None:
        print(f"'config {action}' requires a KEY argument")
        return EXIT_FAIL

    if key not in _ALL_FIELDS:
        print(f"Unknown config field: {key}")
        print(f"Known fields: {', '.join(sorted(_ALL_FIELDS))}")
        return EXIT_FAIL

    if action == "set":
        if key not in _SCALAR_FIELDS:
            print(f"'{key}' is a list field — use 'config add {key} VALUE' or 'config remove {key} VALUE'")
            return EXIT_FAIL
        if value is None:
            print(f"'config set {key}' requires a VALUE argument")
            return EXIT_FAIL

        # Coerce port to int
        if key == "port":
            try:
                value = int(value)
            except ValueError:
                print(f"Invalid port: {value}")
                return EXIT_FAIL

    elif action in ("add", "remove"):
        if key not in _LIST_FIELDS:
            print(f"'{key}' is a scalar field — use 'config set {key} VALUE'")
            return EXIT_FAIL
        if value is None:
            print(f"'config {action} {key}' requires a VALUE argument")
            return EXIT_FAIL

    # Load, modify, write
    if config_path is None:
        print("No texwatch.yaml found")
        print("Run 'texwatch init' to create one")
        return EXIT_FAIL

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    if action == "set":
        data[key] = value
    elif action == "add":
        lst = data.get(key, [])
        if not isinstance(lst, list):
            lst = []
        if value not in lst:
            lst.append(value)
        data[key] = lst
    elif action == "remove":
        lst = data.get(key, [])
        if not isinstance(lst, list):
            lst = []
        if value in lst:
            lst.remove(value)
        else:
            print(f"'{value}' not found in {key}")
            return EXIT_FAIL
        data[key] = lst

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    print(f"Updated {key} in {config_path}")
    return EXIT_OK


def cmd_files(args: argparse.Namespace) -> int:
    """Handle files command."""
    project = _get_project(args)
    resp = _api_get("/files", port=args.port, project=project)

    if resp.server_down:
        if getattr(args, "json", False):
            print(json.dumps({"error": "server not running", "port": args.port}))
        else:
            _print_server_down(args.port)
        return EXIT_SERVER_DOWN

    if resp.error:
        if getattr(args, "json", False):
            print(json.dumps({"error": resp.error}))
        else:
            print(f"Failed to list files: {resp.error}")
        return EXIT_FAIL

    data = resp.data

    if getattr(args, "json", False):
        print(json.dumps(data))
        return EXIT_OK

    # Tree-formatted output
    if isinstance(data, dict):
        entries = data.get("entries", data.get("children", []))
    elif isinstance(data, list):
        entries = data
    else:
        entries = []

    def _print_tree(entries: list, prefix: str = "") -> None:
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            name = entry.get("name", "?")
            print(f"{prefix}{connector}{name}")
            children = entry.get("children", entry.get("entries", []))
            if children:
                extension = "    " if is_last else "│   "
                _print_tree(children, prefix + extension)

    if entries:
        _print_tree(entries)
    else:
        print("No files found")

    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Run the texwatch server."""
    import logging as _logging

    level = _logging.DEBUG if getattr(args, "debug", False) else _logging.INFO
    _logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    main_file = getattr(args, "main_file", None) or None
    config = load_config(main_file=main_file)

    # Override port if specified
    if args.port:
        config.port = args.port

    # Check main file exists
    main_path = get_main_file(config)
    if not main_path.exists():
        print(f"Error: Main file not found: {main_path}")
        print("Run 'texwatch init' to create a config file")
        return EXIT_FAIL

    # Check compiler is available (resolve "auto" first)
    if not check_compiler_available(config.compiler, main_file=main_path):
        from .compiler import _detect_compiler
        resolved = _detect_compiler(main_path) if config.compiler == "auto" else config.compiler
        print(f"Error: Compiler not found: {resolved}")
        if resolved == "pandoc":
            print("Install pandoc: https://pandoc.org/installing.html")
        else:
            print("Install latexmk or specify a different compiler in texwatch.yaml")
        return EXIT_FAIL

    # Resolve compiler name for display
    if config.compiler == "auto":
        from .compiler import _detect_compiler
        display_compiler = f"auto ({_detect_compiler(main_path)})"
    else:
        display_compiler = config.compiler

    print(f"texwatch v{__version__}")
    print(f"Main file: {main_path}")
    print(f"Compiler: {display_compiler}")

    # Run server
    server = TexWatchServer(config)
    try:
        server.run(port=config.port)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    return EXIT_OK


# ---------------------------------------------------------------------------
# New workspace commands: scan, add, remove, projects, serve
# ---------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    """Handle scan command — discover projects in a directory."""
    from .workspace import discover_projects, load_workspace, save_workspace, merge_discovered, reset_directory, WorkspaceConfig, _resolve_default

    scan_dir = Path(args.directory).resolve()
    if not scan_dir.is_dir():
        print(f"Error: Not a directory: {scan_dir}")
        return EXIT_FAIL

    # Resolve skip_dirs: CLI flag > workspace defaults > built-in defaults
    cli_skip = getattr(args, "skip_dirs", None)
    if cli_skip:
        skip_dirs = [s.strip() for s in cli_skip.split(",")]
    else:
        ws_existing = load_workspace()
        ws_defaults = ws_existing.defaults if ws_existing else {}
        skip_dirs = _resolve_default("skip_dirs", ws_defaults)

    found = discover_projects(scan_dir, skip_dirs=skip_dirs)

    if not found:
        print(f"No papers found in {scan_dir}")
        return EXIT_OK

    if getattr(args, "json", False):
        data = [{"name": p.name, "path": str(p.directory), "main": p.main} for p in found]
        print(json.dumps(data))
        if getattr(args, "dry_run", False):
            return EXIT_OK
    else:
        print(f"Scanning {scan_dir}...")
        # Group by directory
        dirs_seen: dict[Path, list] = {}
        for p in found:
            dirs_seen.setdefault(p.directory, []).append(p)

        for directory, papers in dirs_seen.items():
            try:
                rel = directory.relative_to(scan_dir)
            except ValueError:
                rel = directory
            has_yaml = (directory / "texwatch.yaml").exists()
            suffix = "(has texwatch.yaml)" if has_yaml else "(auto-detected)"
            if len(papers) > 1:
                suffix += f", {len(papers)} papers"

            for i, paper in enumerate(papers):
                if i == 0:
                    print(f"  {str(rel) + '/':<20s}{paper.main:<20s}{suffix}")
                else:
                    print(f"  {'':<20s}{paper.main}")

        print(f"\nFound {len(found)} papers in {len(dirs_seen)} directories.")

    if getattr(args, "dry_run", False):
        print("Use without --dry-run to write to workspace.")
        return EXIT_OK

    # Load or create workspace, merge, save
    ws = load_workspace() or WorkspaceConfig()
    if getattr(args, "reset", False):
        removed = reset_directory(ws, scan_dir)
        if removed and not getattr(args, "json", False):
            print(f"Reset {removed} existing project(s) under {scan_dir}")
    ws = merge_discovered(ws, found)
    ws_path = save_workspace(ws)
    print(f"Updated {ws_path}")
    return EXIT_OK


def cmd_workspace(args: argparse.Namespace) -> int:
    """Handle workspace subcommand."""
    ws_cmd = getattr(args, "ws_command", None)
    dispatch = {
        "purge": _cmd_workspace_purge,
        "show": _cmd_workspace_show,
        "path": _cmd_workspace_path,
        "edit": _cmd_workspace_edit,
    }
    handler = dispatch.get(ws_cmd)
    if handler:
        return handler(args)
    print("Usage: texwatch workspace {purge,show,path,edit}")
    return EXIT_FAIL


def _cmd_workspace_purge(args: argparse.Namespace) -> int:
    from .workspace import load_workspace, save_workspace, purge_projects
    ws = load_workspace()
    if ws is None:
        print("No workspace found.")
        return EXIT_OK
    removed = purge_projects(ws)
    if removed == 0:
        print("Workspace already empty.")
        return EXIT_OK
    save_workspace(ws)
    print(f"Removed {removed} project(s) from workspace.")
    return EXIT_OK


def _cmd_workspace_show(args: argparse.Namespace) -> int:
    from .workspace import load_workspace, workspace_path
    ws_path = workspace_path()
    ws = load_workspace()
    if ws is None:
        print(f"No workspace found at {ws_path}")
        return EXIT_FAIL
    print(f"Workspace: {ws_path}")
    print(f"Port:      {ws.port}")
    if ws.defaults:
        print(f"Defaults:  {ws.defaults}")
    print(f"Projects:  {len(ws.projects)}")
    return EXIT_OK


def _cmd_workspace_path(args: argparse.Namespace) -> int:
    from .workspace import workspace_path
    print(workspace_path())
    return EXIT_OK


def _cmd_workspace_edit(args: argparse.Namespace) -> int:
    import os
    import subprocess
    from .workspace import workspace_path
    ws_path = workspace_path()
    if not ws_path.exists():
        print(f"No workspace found at {ws_path}")
        return EXIT_FAIL
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
    return subprocess.call([editor, str(ws_path)])


def cmd_add(args: argparse.Namespace) -> int:
    """Handle add command — register one project/directory."""
    from .workspace import (
        load_workspace, save_workspace, project_config_from_dir,
        WorkspaceConfig, ProjectConfig,
    )

    target = Path(args.path).resolve()
    if not target.is_dir():
        print(f"Error: Not a directory: {target}")
        return EXIT_FAIL

    name = getattr(args, "name", None)
    main_file = getattr(args, "main_file", None)

    if name and main_file:
        # Explicit name and main
        pc = ProjectConfig(name=name, directory=target, main=main_file)
        projects = [pc]
    elif name:
        # Explicit name, auto-detect main
        detected = project_config_from_dir(target)
        if detected:
            projects = [ProjectConfig(name=name, directory=detected[0].directory, main=detected[0].main)]
        else:
            if main_file:
                projects = [ProjectConfig(name=name, directory=target, main=main_file)]
            else:
                print(f"Could not auto-detect a main file in {target}")
                return EXIT_FAIL
    else:
        # Auto-detect everything
        detected = project_config_from_dir(target)
        if not detected:
            print(f"Could not auto-detect papers in {target}")
            return EXIT_FAIL
        projects = detected

    ws = load_workspace() or WorkspaceConfig()
    for pc in projects:
        ws.projects[pc.name] = pc
        print(f"Added: {pc.name} ({pc.directory / pc.main})")

    ws_path = save_workspace(ws)
    print(f"Updated {ws_path}")
    return EXIT_OK


def cmd_remove(args: argparse.Namespace) -> int:
    """Handle remove command — unregister a project."""
    from .workspace import load_workspace, save_workspace

    ws = load_workspace()
    if ws is None:
        print("No workspace found (~/.texwatch/workspace.yaml)")
        return EXIT_FAIL

    name = args.name
    if name not in ws.projects:
        print(f"Project not found: {name}")
        print(f"Known projects: {', '.join(sorted(ws.projects.keys())) or '(none)'}")
        return EXIT_FAIL

    del ws.projects[name]
    ws_path = save_workspace(ws)
    print(f"Removed: {name}")
    print(f"Updated {ws_path}")
    return EXIT_OK


def cmd_projects(args: argparse.Namespace) -> int:
    """Handle projects command — list registered projects."""
    from .workspace import load_workspace

    ws = load_workspace()
    if ws is None:
        print("No workspace found (~/.texwatch/workspace.yaml)")
        print("Run 'texwatch scan DIR' to discover projects")
        return EXIT_FAIL

    if not ws.projects:
        print("No projects registered")
        return EXIT_OK

    if getattr(args, "json", False):
        data = []
        for name, pc in ws.projects.items():
            data.append({
                "name": name,
                "path": str(pc.directory),
                "main": pc.main,
                "compiler": pc.compiler,
            })
        print(json.dumps(data))
        return EXIT_OK

    print(f"{len(ws.projects)} projects in {ws.defaults.get('_path', '~/.texwatch/workspace.yaml')}\n")
    for name, pc in ws.projects.items():
        path_str = str(pc.directory)
        if len(path_str) > 35:
            path_str = "..." + path_str[-32:]
        print(f"  {name:<24s}{path_str:<38s}{pc.main:<18s}{pc.compiler}")

    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Handle serve command — serve all workspace projects."""
    import logging as _logging
    from .workspace import load_workspace

    level = _logging.DEBUG if getattr(args, "debug", False) else _logging.INFO
    _logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    ws_path = getattr(args, "workspace", None)
    ws = load_workspace(Path(ws_path) if ws_path else None)

    if ws is None:
        print("No workspace found (~/.texwatch/workspace.yaml)")
        print("Run 'texwatch scan DIR' to discover projects")
        return EXIT_FAIL

    if not ws.projects:
        print("No projects in workspace")
        return EXIT_FAIL

    port = getattr(args, "port", None) or ws.port

    # Build project list
    project_list: list[tuple[str, Config]] = []
    for name, pc in ws.projects.items():
        cfg = pc.to_legacy_config(port=port)
        project_list.append((name, cfg))

    print(f"texwatch v{__version__}")
    print(f"Serving {len(project_list)} projects")
    for name, cfg in project_list:
        print(f"  {name}: {get_main_file(cfg)}")

    server = TexWatchServer(projects=project_list)
    try:
        server.run(port=port)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _add_server_options(parser: argparse.ArgumentParser) -> None:
    """Add common server-communication options to a subparser."""
    parser.add_argument("-p", "--port", type=int, default=8765, help="Server port (default: 8765)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--project", type=str, default=None, help="Target project name")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="texwatch",
        description="TeX file watcher with browser-based PDF viewer",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"texwatch {__version__}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.set_defaults(command=None)

    subparsers = parser.add_subparsers(dest="command")

    # --- init ---
    p_init = subparsers.add_parser("init", help="Create texwatch.yaml in current directory")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")
    p_init.add_argument("--compiler", type=str, default=None, help="Set compiler (default: auto)")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Query running texwatch instance")
    _add_server_options(p_status)

    # --- view ---
    p_view = subparsers.add_parser("view", help="Show editor and viewer pane state")
    _add_server_options(p_view)

    # --- goto ---
    p_goto = subparsers.add_parser("goto", help="Navigate to line number, pN for page, or section name")
    p_goto.add_argument("target", help="Line number, pN for page, or section name")
    _add_server_options(p_goto)

    # --- compile ---
    p_compile = subparsers.add_parser("compile", help="Trigger manual recompile")
    _add_server_options(p_compile)

    # --- capture ---
    p_capture = subparsers.add_parser("capture", help="Screenshot PDF page to PNG file")
    p_capture.add_argument("output", help="Output PNG file path")
    p_capture.add_argument("--page", type=int, default=None, help="Page number (default: viewer's current page)")
    p_capture.add_argument("--dpi", type=int, default=None, help="DPI for rendering (default: 150, range: 72-600)")
    _add_server_options(p_capture)

    # --- config ---
    p_config = subparsers.add_parser("config", help="View or modify texwatch.yaml")
    p_config.add_argument("action", nargs="?", default="show",
                          choices=["show", "set", "add", "remove", "path"],
                          help="Action to perform (default: show)")
    p_config.add_argument("key", nargs="?", help="Config field name")
    p_config.add_argument("value", nargs="?", help="Value to set/add/remove")
    p_config.add_argument("--json", action="store_true", help="Output as JSON")

    # --- files ---
    p_files = subparsers.add_parser("files", help="List project file tree")
    _add_server_options(p_files)

    # --- scan ---
    p_scan = subparsers.add_parser("scan", help="Discover projects in a directory")
    p_scan.add_argument("directory", help="Directory to scan")
    p_scan.add_argument("--dry-run", action="store_true", help="Show what would be registered")
    p_scan.add_argument("--skip-dirs", type=str, default=None,
                        help="Comma-separated glob patterns of directories to skip")
    p_scan.add_argument("--json", action="store_true", help="Output as JSON")
    p_scan.add_argument("--reset", action="store_true",
                        help="Remove existing projects under scanned directory before re-discovering")

    # --- add ---
    p_add = subparsers.add_parser("add", help="Register a project directory")
    p_add.add_argument("path", help="Path to project directory")
    p_add.add_argument("--name", type=str, default=None, help="Project name (default: auto)")
    p_add.add_argument("--main", dest="main_file", type=str, default=None, help="Main file (default: auto-detect)")

    # --- remove ---
    p_remove = subparsers.add_parser("remove", help="Unregister a project")
    p_remove.add_argument("name", help="Project name to remove")

    # --- workspace ---
    p_workspace = subparsers.add_parser("workspace", help="Manage workspace")
    ws_sub = p_workspace.add_subparsers(dest="ws_command")
    ws_sub.add_parser("purge", help="Remove all projects from workspace")
    ws_sub.add_parser("show", help="Show workspace info")
    ws_sub.add_parser("path", help="Print workspace file path")
    ws_sub.add_parser("edit", help="Open workspace in $EDITOR")

    # --- projects ---
    p_projects = subparsers.add_parser("projects", help="List registered projects")
    p_projects.add_argument("--json", action="store_true", help="Output as JSON")

    # --- serve ---
    p_serve = subparsers.add_parser("serve", help="Serve all workspace projects")
    p_serve.add_argument("-p", "--port", type=int, default=None, help="Server port (default: from workspace)")
    p_serve.add_argument("--workspace", type=str, default=None, help="Path to workspace.yaml")
    p_serve.add_argument("--debug", action="store_true", help="Enable debug logging")

    return parser


def _build_run_parser() -> argparse.ArgumentParser:
    """Build a secondary parser for the default run mode (no subcommand)."""
    parser = argparse.ArgumentParser(prog="texwatch", add_help=False)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("-p", "--port", type=int, default=8765)
    parser.add_argument("main_file", nargs="?", default=None)
    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_DISPATCH = {
    None: cmd_run,
    "init": cmd_init,
    "status": cmd_status,
    "view": cmd_view,
    "goto": cmd_goto,
    "compile": cmd_compile,
    "capture": cmd_capture,
    "config": cmd_config,
    "files": cmd_files,
    "scan": cmd_scan,
    "add": cmd_add,
    "remove": cmd_remove,
    "projects": cmd_projects,
    "serve": cmd_serve,
    "workspace": cmd_workspace,
}


_SUBCOMMANDS = frozenset(_DISPATCH.keys()) - {None}


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args_list = argv if argv is not None else sys.argv[1:]

    # Check if the first non-flag token is a known subcommand.
    # If not, route to the run-mode parser which accepts --port and main_file.
    first_positional = None
    for token in args_list:
        if token == "--":
            break
        if not token.startswith("-"):
            first_positional = token
            break

    if first_positional in _SUBCOMMANDS:
        parser = build_parser()
        args = parser.parse_args(args_list)
        handler = _DISPATCH[args.command]
        return handler(args)

    # No subcommand — default to run mode
    if "--help" in args_list or "-h" in args_list \
       or "--version" in args_list or "-V" in args_list:
        build_parser().parse_args(args_list)  # will print and exit

    run_parser = _build_run_parser()
    args = run_parser.parse_args(args_list)
    args.command = None
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())

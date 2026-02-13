"""Command-line interface for texwatch."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from . import __version__
from .config import create_config, find_config, get_main_file
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
    """Result of an HTTP request to the texwatch server.

    Attributes:
        status: HTTP status code (0 if connection failed).
        data: Response body (dict for JSON, bytes for binary).
        error: Error message if request failed.
        server_down: True if the server is not running.
        auto_project: Project name when server auto-selected via current pointer.
    """

    status: int
    data: dict | bytes | None = None
    error: str | None = None
    server_down: bool = False
    auto_project: str | None = None


def _handle_http_error(e: HTTPError) -> APIResponse:
    """Convert HTTPError to APIResponse with error message."""
    try:
        raw = e.read().decode()
        try:
            body = json.loads(raw)
            error_msg = body.get("error", f"HTTP {e.code}")
        except json.JSONDecodeError:
            # Plain text error (e.g. "Multi-project server: use /p/{name}/files")
            error_msg = raw.strip() if raw.strip() else f"HTTP {e.code}"
    except UnicodeDecodeError:
        error_msg = f"HTTP {e.code}"
    return APIResponse(status=e.code, error=error_msg)


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
            auto_project = response.headers.get("X-Texwatch-Project")
            raw = response.read()
            if "application/json" in content_type:
                data = json.loads(raw.decode())
            else:
                data = raw
            return APIResponse(status=response.status, data=data,
                               auto_project=auto_project)
    except HTTPError as e:
        return _handle_http_error(e)
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
            auto_project = response.headers.get("X-Texwatch-Project")
            result = json.loads(response.read().decode())
            return APIResponse(status=response.status, data=result,
                               auto_project=auto_project)
    except HTTPError as e:
        return _handle_http_error(e)
    except URLError:
        return APIResponse(status=0, server_down=True)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


@dataclass
class GotoResult:
    """Result of a goto command to the texwatch server.

    Attributes:
        success: True if navigation succeeded.
        error: Error message if navigation failed.
        server_down: True if the server is not running.
    """

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


def _parse_goto_target(target: str) -> dict:
    """Parse a goto target string into a request payload dict."""
    if target.isdigit():
        return {"line": int(target)}
    if target.startswith("p") and target[1:].isdigit():
        return {"page": int(target[1:])}
    return {"section": target}


def _goto_response_to_result(resp: APIResponse) -> GotoResult:
    """Convert an APIResponse from /goto into a GotoResult."""
    if resp.server_down:
        return GotoResult(success=False, server_down=True)
    if resp.error:
        return GotoResult(success=False, error=resp.error)
    if isinstance(resp.data, dict):
        return GotoResult(success=resp.data.get("success", False))
    return GotoResult(success=False, error="Unexpected response")


def send_goto(target: str, port: int = 8765) -> GotoResult:
    """Send goto command to running instance."""
    resp = _api_post("/goto", _parse_goto_target(target), port=port)
    return _goto_response_to_result(resp)


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
    config_path = Path.cwd() / ".texwatch.yaml"

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


def _detect_multi_project(args: argparse.Namespace) -> list[str] | None:
    """Probe /projects. Return project names if multi-project, else None."""
    probe = _api_get("/projects", port=args.port)
    if probe.server_down or not isinstance(probe.data, dict):
        return None
    projects = probe.data.get("projects", [])
    if len(projects) <= 1:
        return None
    return [p["name"] for p in projects]


def _print_multi_project_required(command: str, project_names: list[str]) -> None:
    """Print helpful error when a command requires --project in multi-project mode."""
    print(f"Error: {command} requires --project in multi-project mode.")
    print("Available projects:")
    for name in project_names:
        print(f"  {name}")


def _reject_if_multi_project(
    command: str, args: argparse.Namespace,
) -> int | None:
    """Return EXIT_FAIL if the server is multi-project and --project is not set.

    Returns None when the command may proceed (single-project or --project given).
    """
    if _get_project(args):
        return None
    project_names = _detect_multi_project(args)
    if project_names is None:
        return None
    if getattr(args, "json", False):
        print(json.dumps({
            "error": f"{command} requires --project in multi-project mode",
            "projects": project_names,
        }))
    else:
        _print_multi_project_required(command, project_names)
    return EXIT_FAIL


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
        # Fall back to single-project status
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

    # Multi-project aggregate when no --project
    if not project:
        project_names = _detect_multi_project(args)
        if project_names is not None:
            return _view_all_projects(project_names, args)

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


def _view_all_projects(project_names: list[str], args: argparse.Namespace) -> int:
    """Show viewer state for all projects."""
    all_views: dict = {}
    for name in project_names:
        resp = _api_get("/status", port=args.port, project=name)
        if resp.error or not isinstance(resp.data, dict):
            all_views[name] = {"error": resp.error or "unknown"}
            continue
        status = resp.data
        all_views[name] = {
            "main_file": status.get("file"),
            "editor": status.get("editor", {}),
            "viewer": status.get("viewer", {}),
        }

    if getattr(args, "json", False):
        print(json.dumps(all_views))
        return EXIT_OK

    for name, view in all_views.items():
        print(f"{name}:")
        if "error" in view:
            print(f"  (error: {view['error']})")
            continue
        editor = view.get("editor", {})
        viewer = view.get("viewer", {})
        file_str = editor.get("file") or "(no file)"
        line_str = f":{editor['line']}" if editor.get("line") else ""
        page_str = ""
        if viewer.get("page"):
            page_str = f"Page {viewer['page']}/{viewer.get('total_pages', '?')}"
        print(f"  File: {file_str}{line_str}  {page_str}")

    return EXIT_OK


def _print_auto_project(resp: APIResponse) -> None:
    """Print auto-selected project hint if present."""
    if resp.auto_project:
        print(f"(using project: {resp.auto_project})")


def cmd_goto(args: argparse.Namespace) -> int:
    """Handle goto command."""
    rejected = _reject_if_multi_project("goto", args)
    if rejected is not None:
        return rejected

    project = _get_project(args)
    data = _parse_goto_target(args.target)
    resp = _api_post("/goto", data, port=args.port, project=project)
    result = _goto_response_to_result(resp)

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

    _print_auto_project(resp)
    if result.success:
        print(f"Navigated to: {args.target}")
        return EXIT_OK

    print(f"Failed to navigate to: {args.target}")
    if result.error:
        print(result.error)
    return EXIT_FAIL


def cmd_capture(args: argparse.Namespace) -> int:
    """Handle capture command."""
    rejected = _reject_if_multi_project("capture", args)
    if rejected is not None:
        return rejected

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
            _print_auto_project(resp)
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

    # Multi-project mode without --project: compile all via aggregate /compile
    if not project:
        project_names = _detect_multi_project(args)
        if project_names is not None:
            return _compile_all_projects(args)

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


def _compile_all_projects(args: argparse.Namespace) -> int:
    """Compile all projects via the aggregate /compile endpoint."""
    resp = _api_post("/compile", {}, port=args.port)

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

    if getattr(args, "json", False):
        print(json.dumps(data))
        return EXIT_OK

    projects = data.get("projects", {})
    all_ok = True
    for name, result in projects.items():
        if result is None:
            print(f"  {name}: no result")
            all_ok = False
            continue
        success = result.get("success", False)
        if not success:
            all_ok = False
        duration = result.get("duration_seconds")
        dur_str = f" ({duration:.1f}s)" if duration is not None else ""
        err_count = len(result.get("errors", []))
        if success:
            print(f"  {name}: ok{dur_str}")
        else:
            print(f"  {name}: failed, {err_count} errors{dur_str}")

    return EXIT_OK if all_ok else EXIT_FAIL


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
            print("No .texwatch.yaml found")
            return EXIT_FAIL
        return EXIT_OK

    if action == "show":
        if config_path is None:
            if getattr(args, "json", False):
                print(json.dumps({"error": "no config file found"}))
            else:
                print("No .texwatch.yaml found")
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
        print("No .texwatch.yaml found")
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


def _print_file_tree(entries: list, prefix: str = "") -> None:
    """Print a file tree with box-drawing connectors."""
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        name = entry.get("name", "?")
        print(f"{prefix}{connector}{name}")
        children = entry.get("children", entry.get("entries", []))
        if children:
            extension = "    " if is_last else "│   "
            _print_file_tree(children, prefix + extension)


def _extract_file_entries(data: object) -> list:
    """Extract file entries from a /files response."""
    if isinstance(data, dict):
        entries = data.get("entries") or data.get("children") or []
        return entries  # type: ignore[return-value]
    if isinstance(data, list):
        return data
    return []


def _files_all_projects(data: dict, args: argparse.Namespace) -> int:
    """List files for all projects in multi-project mode."""
    projects = data.get("projects", [])

    if getattr(args, "json", False):
        all_files: dict = {}
        for p in projects:
            name = p.get("name", "?")
            resp = _api_get("/files", port=args.port, project=name)
            if not resp.error:
                all_files[name] = resp.data
        print(json.dumps(all_files))
        return EXIT_OK

    for p in projects:
        name = p.get("name", "?")
        resp = _api_get("/files", port=args.port, project=name)
        print(f"{name}/")
        if resp.error:
            print(f"  (error: {resp.error})")
            continue

        entries = _extract_file_entries(resp.data)
        if entries:
            _print_file_tree(entries, prefix="  ")
        else:
            print("  (no files)")

    return EXIT_OK


def cmd_files(args: argparse.Namespace) -> int:
    """Handle files command."""
    project = _get_project(args)

    # In multi-project mode without --project, list files for all projects
    if not project:
        probe = _api_get("/projects", port=args.port)
        if not probe.server_down and isinstance(probe.data, dict) and "projects" in probe.data:
            return _files_all_projects(probe.data, args)

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
    entries = _extract_file_entries(data)
    if entries:
        _print_file_tree(entries)
    else:
        print("No files found")

    return EXIT_OK


def cmd_activity(args: argparse.Namespace) -> int:
    """Handle activity command — show recent events."""
    params = []
    limit = getattr(args, "limit", None)
    if limit is not None:
        params.append(f"limit={limit}")
    event_type = getattr(args, "type", None)
    if event_type:
        params.append(f"type={event_type}")
    query = f"?{'&'.join(params)}" if params else ""

    result = _fetch_endpoint(f"/activity{query}", "activity", args, reject_multi=False)
    if isinstance(result, int):
        return result
    data, _ = result

    events = data.get("events", [])
    if not events:
        print("No activity recorded yet")
        return EXIT_OK

    for ev in events:
        ts = ev.get("timestamp", "?")
        if "T" in ts:
            ts = ts.split("T")[1][:8]
        etype = ev.get("type", "?")
        proj_name = ev.get("project", "")
        extra_parts = [
            f"{k}={v}" for k, v in ev.items()
            if k not in ("type", "timestamp", "project")
        ]
        extra = " ".join(extra_parts)
        print(f"  {ts}  {proj_name:<16s} {etype:<16s} {extra}")

    return EXIT_OK


def _fetch_endpoint(
    endpoint: str,
    command_name: str,
    args: argparse.Namespace,
    *,
    reject_multi: bool = True,
) -> tuple[dict, APIResponse] | int:
    """Fetch a JSON endpoint with standard error handling.

    Handles multi-project rejection, server-down, HTTP errors, and JSON
    output mode.  Returns either the parsed (data, response) tuple on
    success, or an exit code on failure.
    """
    if reject_multi:
        rejected = _reject_if_multi_project(command_name, args)
        if rejected is not None:
            return rejected

    project = _get_project(args)
    resp = _api_get(endpoint, port=args.port, project=project)

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
            print(f"Failed to get {command_name}: {resp.error}")
        return EXIT_FAIL

    data = resp.data
    if not isinstance(data, dict):
        return EXIT_FAIL

    if getattr(args, "json", False):
        print(json.dumps(data))
        return EXIT_OK

    _print_auto_project(resp)
    return data, resp


def cmd_bibliography(args: argparse.Namespace) -> int:
    """Handle bibliography command — show bibliography analysis."""
    result = _fetch_endpoint("/bibliography", "bibliography", args)
    if isinstance(result, int):
        return result
    data, _ = result

    entries = data.get("entries", [])
    citations = data.get("citations", [])
    uncited = data.get("uncited_keys", [])
    undefined = data.get("undefined_keys", [])

    print(f"Bibliography entries: {len(entries)}")
    for e in entries:
        fields = e.get("fields", {})
        author = fields.get("author", "")
        year = fields.get("year", "")
        title = fields.get("title", "")
        print(f"  [{e['key']}] {author} ({year}) {title}")

    print(f"\nCitations: {len(citations)}")

    if uncited:
        print(f"\nUncited entries ({len(uncited)}):")
        for key in uncited:
            print(f"  {key}")

    if undefined:
        print(f"\nUndefined citations ({len(undefined)}):")
        for key in undefined:
            print(f"  {key}")

    return EXIT_OK


def cmd_environments(args: argparse.Namespace) -> int:
    """Handle environments command — list LaTeX environments."""
    result = _fetch_endpoint("/environments", "environments", args)
    if isinstance(result, int):
        return result
    data, _ = result

    envs = data.get("environments", [])

    if not envs:
        print("No tracked environments found")
        return EXIT_OK

    print(f"Environments ({len(envs)}):")
    for e in envs:
        label_str = f" [{e['label']}]" if e.get("label") else ""
        name_str = f" \"{e['name']}\"" if e.get("name") else ""
        caption_str = f" — {e['caption']}" if e.get("caption") else ""
        lines_str = f"{e['start_line']}"
        if e.get("end_line"):
            lines_str += f"-{e['end_line']}"
        print(f"  {e['env_type']}{name_str}{label_str}{caption_str}  ({e['file']}:{lines_str})")

    return EXIT_OK


def cmd_digest(args: argparse.Namespace) -> int:
    """Handle digest command — show document metadata."""
    result = _fetch_endpoint("/digest", "digest", args)
    if isinstance(result, int):
        return result
    data, _ = result

    if data.get("documentclass"):
        opts = ", ".join(data.get("class_options", []))
        opts_str = f"[{opts}]" if opts else ""
        print(f"Document class: {data['documentclass']}{opts_str}")

    if data.get("title"):
        print(f"Title: {data['title']}")
    if data.get("author"):
        print(f"Author: {data['author']}")
    if data.get("date"):
        print(f"Date: {data['date']}")

    packages = data.get("packages", [])
    if packages:
        print(f"\nPackages ({len(packages)}):")
        for p in packages:
            opts_str = f"[{p['options']}]" if p.get("options") else ""
            print(f"  {p['name']}{opts_str}")

    commands = data.get("commands", [])
    if commands:
        print(f"\nCustom commands ({len(commands)}):")
        for c in commands:
            args_str = f"[{c['args']}]" if c.get("args") is not None else ""
            print(f"  {c['name']}{args_str} = {c['definition']}")

    if data.get("abstract"):
        abstract = data["abstract"]
        if len(abstract) > 200:
            abstract = abstract[:200] + "..."
        print(f"\nAbstract: {abstract}")

    return EXIT_OK


def cmd_scan(args: argparse.Namespace) -> int:
    """List directories containing .texwatch.yaml."""
    from .workspace import discover_projects

    scan_dir = Path(args.directory).resolve()
    if not scan_dir.is_dir():
        print(f"Error: Not a directory: {scan_dir}")
        return EXIT_FAIL

    cli_skip = getattr(args, "skip_dirs", None)
    skip_dirs = [s.strip() for s in cli_skip.split(",")] if cli_skip else None
    found = discover_projects(scan_dir, skip_dirs=skip_dirs)

    if not found:
        print(f"No projects found in {scan_dir}")
        return EXIT_OK

    if getattr(args, "json", False):
        data = [{"name": p.name, "path": str(p.directory), "main": p.main} for p in found]
        print(json.dumps(data))
        return EXIT_OK

    dirs_seen: dict[Path, list] = {}
    for p in found:
        dirs_seen.setdefault(p.directory, []).append(p)
    for directory, papers in dirs_seen.items():
        try:
            rel = directory.relative_to(scan_dir)
        except ValueError:
            rel = directory
        for i, paper in enumerate(papers):
            if i == 0:
                suffix = f" ({len(papers)} papers)" if len(papers) > 1 else ""
                print(f"  {str(rel) + '/':<30s}{paper.main}{suffix}")
            else:
                print(f"  {'':<30s}{paper.main}")

    print(f"\nFound {len(found)} projects in {len(dirs_seen)} directories.")
    return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    """Handle mcp command — run the MCP stdio server."""
    try:
        from . import mcp_server
    except ImportError:
        print(
            "Error: MCP server requires 'mcp' and 'httpx' packages.\n"
            "Install them with:\n"
            "  pip install 'mcp>=1.0' httpx\n"
            "Or install the optional dependency group:\n"
            "  pip install texwatch[mcp]",
            file=sys.stderr,
        )
        return EXIT_FAIL

    port = getattr(args, "port", 8765)
    project = getattr(args, "project", None) or None
    mcp_server.main(port=port, project=project)
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve projects from .texwatch.yaml files."""
    import logging as _logging
    from .workspace import discover_projects, project_config_from_dir

    level = _logging.DEBUG if getattr(args, "debug", False) else _logging.INFO
    _logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    serve_dir = Path(getattr(args, "dir", None) or ".").resolve()
    recursive = getattr(args, "recursive", False)
    port = getattr(args, "port", None) or 8765

    if not serve_dir.is_dir():
        print(f"Error: Not a directory: {serve_dir}")
        return EXIT_FAIL

    if recursive:
        skip_str = getattr(args, "skip_dirs", None)
        skip_dirs = [s.strip() for s in skip_str.split(",")] if skip_str else None
        found = discover_projects(serve_dir, skip_dirs=skip_dirs)
    else:
        found = project_config_from_dir(serve_dir)

    if not found:
        yaml_path = serve_dir / ".texwatch.yaml"
        if not yaml_path.exists():
            print(f"No .texwatch.yaml found in {serve_dir}")
            print("Run 'texwatch init' to create one")
        else:
            print(f"No projects found in {serve_dir}")
        return EXIT_FAIL

    project_list = [(pc.name, pc.to_legacy_config(port=port)) for pc in found]

    print(f"texwatch v{__version__}")
    if len(project_list) == 1:
        name, cfg = project_list[0]
        main_path = get_main_file(cfg)
        if not check_compiler_available(cfg.compiler, main_file=main_path):
            from .compiler import _detect_compiler
            resolved = _detect_compiler(main_path) if cfg.compiler == "auto" else cfg.compiler
            print(f"Error: Compiler not found: {resolved}")
            return EXIT_FAIL
        print(f"Main file: {main_path}")
    else:
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
    p_init = subparsers.add_parser("init", help="Create .texwatch.yaml in current directory")
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
    p_config = subparsers.add_parser("config", help="View or modify .texwatch.yaml")
    p_config.add_argument("action", nargs="?", default="show",
                          choices=["show", "set", "add", "remove", "path"],
                          help="Action to perform (default: show)")
    p_config.add_argument("key", nargs="?", help="Config field name")
    p_config.add_argument("value", nargs="?", help="Value to set/add/remove")
    p_config.add_argument("--json", action="store_true", help="Output as JSON")

    # --- files ---
    p_files = subparsers.add_parser("files", help="List project file tree")
    _add_server_options(p_files)

    # --- activity ---
    p_activity = subparsers.add_parser("activity", help="Show recent activity events")
    p_activity.add_argument("--type", type=str, default=None,
                            help="Filter by event type (e.g. compile_finish, goto, page_view)")
    p_activity.add_argument("--limit", type=int, default=None,
                            help="Maximum number of events (default: 50)")
    _add_server_options(p_activity)

    # --- bibliography ---
    p_bib = subparsers.add_parser("bibliography", help="Show bibliography analysis")
    _add_server_options(p_bib)

    # --- environments ---
    p_envs = subparsers.add_parser("environments", help="List LaTeX environments")
    _add_server_options(p_envs)

    # --- digest ---
    p_digest = subparsers.add_parser("digest", help="Show document metadata")
    _add_server_options(p_digest)

    # --- scan ---
    p_scan = subparsers.add_parser("scan", help="List projects (directories with .texwatch.yaml)")
    p_scan.add_argument("directory", help="Directory to scan")
    p_scan.add_argument("--skip-dirs", type=str, default=None, help="Comma-separated skip patterns")
    p_scan.add_argument("--json", action="store_true", help="Output as JSON")

    # --- mcp ---
    p_mcp = subparsers.add_parser("mcp", help="Run MCP (Model Context Protocol) stdio server")
    p_mcp.add_argument("-p", "--port", type=int, default=8765, help="Server port (default: 8765)")
    p_mcp.add_argument("--project", type=str, default=None, help="Target project name")

    # --- serve ---
    p_serve = subparsers.add_parser("serve", help="Serve projects from .texwatch.yaml")
    p_serve.add_argument("--dir", type=str, default=".", help="Root directory (default: .)")
    p_serve.add_argument("--recursive", action="store_true", help="Walk tree for .texwatch.yaml files")
    p_serve.add_argument("--skip-dirs", type=str, default=None, help="Comma-separated skip patterns (recursive)")
    p_serve.add_argument("-p", "--port", type=int, default=None, help="Server port (default: 8765)")
    p_serve.add_argument("--debug", action="store_true", help="Enable debug logging")

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_DISPATCH = {
    None: cmd_serve,
    "init": cmd_init,
    "status": cmd_status,
    "view": cmd_view,
    "goto": cmd_goto,
    "compile": cmd_compile,
    "capture": cmd_capture,
    "config": cmd_config,
    "files": cmd_files,
    "activity": cmd_activity,
    "bibliography": cmd_bibliography,
    "environments": cmd_environments,
    "digest": cmd_digest,
    "scan": cmd_scan,
    "serve": cmd_serve,
    "mcp": cmd_mcp,
}


_SUBCOMMANDS = frozenset(_DISPATCH.keys()) - {None}


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args_list = argv if argv is not None else sys.argv[1:]

    # Check if the first non-flag token is a known subcommand.
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

    # No subcommand — default to serve --dir .
    if "--help" in args_list or "-h" in args_list \
       or "--version" in args_list or "-V" in args_list:
        build_parser().parse_args(args_list)  # prints and exits

    parser = build_parser()
    args = parser.parse_args(["serve"] + args_list)
    return cmd_serve(args)


if __name__ == "__main__":
    sys.exit(main())

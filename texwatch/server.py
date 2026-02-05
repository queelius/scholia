"""aiohttp web server with HTTP API and WebSocket support.

Supports both single-project (legacy) and multi-project mode.
In multi-project mode, each project is served under /p/{name}/.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType

from .compiler import CompileMessage, CompileResult, compile_tex
from .config import Config, get_main_file, get_watch_dir
from .structure import parse_structure
from .synctex import (
    SyncTeXData,
    find_synctex_file,
    get_visible_lines,
    parse_synctex,
    source_to_page,
)
from .watcher import TexWatcher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ProjectInstance — per-project runtime state
# ---------------------------------------------------------------------------


class ProjectInstance:
    """Per-project runtime state."""

    def __init__(self, config: Config, name: str = ""):
        self.config = config
        self.name = name
        self.compiling = False
        self.last_result: CompileResult | None = None
        self.synctex_data: SyncTeXData | None = None
        self.viewer_state: dict[str, Any] = {
            "page": 1,
            "total_pages": 0,
            "visible_lines": None,
        }
        self.editor_state: dict[str, Any] = {
            "file": None,
            "line": None,
        }
        self.websockets: set[web.WebSocketResponse] = set()
        self.watcher: TexWatcher | None = None

    async def broadcast(self, message: dict) -> None:
        """Broadcast message to all WebSocket clients for this project."""
        if not self.websockets:
            return
        for ws in list(self.websockets):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                self.websockets.discard(ws)

    async def send_state(self, ws: web.WebSocketResponse) -> None:
        """Send current state to a WebSocket client."""
        await ws.send_json({
            "type": "state",
            "compiling": self.compiling,
            "result": _result_to_dict(self.last_result) if self.last_result else None,
            "viewer": self.viewer_state,
            "editor": self.editor_state,
        })

    async def do_compile(self) -> None:
        """Run compilation for this project."""
        self.compiling = True
        await self.broadcast({"type": "compiling", "status": True})

        try:
            main_file = get_main_file(self.config)
            work_dir = get_watch_dir(self.config)

            self.last_result = await compile_tex(
                main_file=main_file,
                compiler=self.config.compiler,
                work_dir=work_dir,
            )

            # Reload SyncTeX data (only for LaTeX files)
            if self.last_result.output_file:
                if main_file.suffix.lower() == ".tex":
                    synctex_path = find_synctex_file(self.last_result.output_file)
                    if synctex_path:
                        self.synctex_data = parse_synctex(synctex_path)
                        if self.synctex_data:
                            logger.debug(
                                "SyncTeX loaded: %s (%d files, %d source->pdf mappings, %d pages)",
                                synctex_path.name,
                                len(self.synctex_data.input_files),
                                len(self.synctex_data.source_to_pdf),
                                len(self.synctex_data.pdf_to_source),
                            )
                        else:
                            logger.debug("SyncTeX parse failed: %s", synctex_path.name)
                    else:
                        logger.debug(
                            "SyncTeX file not found for %s",
                            self.last_result.output_file.name,
                        )
                else:
                    self.synctex_data = None

                self.viewer_state["total_pages"] = 0  # Set by browser

            logger.info(
                f"[{self.name}] Compilation {'succeeded' if self.last_result.success else 'failed'} "
                f"in {self.last_result.duration_seconds:.2f}s"
            )

        finally:
            self.compiling = False
            await self.broadcast({"type": "compiling", "status": False})
            msg = {
                "type": "compiled",
                "result": _result_to_dict(self.last_result) if self.last_result else None,
            }
            if self.last_result and self.last_result.log_output:
                msg["log_output"] = self.last_result.log_output
            await self.broadcast(msg)

    async def on_file_change(self, changed_path: str) -> None:
        """Handle file change from watcher."""
        logger.info(f"[{self.name}] File change detected ({changed_path}), recompiling...")
        try:
            p = Path(changed_path)
            await self.broadcast({
                "type": "source_updated",
                "file": p.name,
                "mtime_ns": str(p.stat().st_mtime_ns),
            })
        except OSError:
            pass
        await self.do_compile()

    def status_summary(self) -> dict[str, Any]:
        """Return a summary dict for the /projects endpoint."""
        return {
            "name": self.name,
            "path": str(get_watch_dir(self.config)),
            "main": self.config.main,
            "compiler": self.config.compiler,
            "compiling": self.compiling,
            "last_compile": (
                self.last_result.timestamp.isoformat() if self.last_result else None
            ),
            "success": self.last_result.success if self.last_result else None,
            "error_count": len(self.last_result.errors) if self.last_result else 0,
            "warning_count": len(self.last_result.warnings) if self.last_result else 0,
            "viewer": self.viewer_state,
        }


# ---------------------------------------------------------------------------
# Module-level serialisation helpers
# ---------------------------------------------------------------------------


def _result_to_dict(result: CompileResult) -> dict[str, Any]:
    """Convert CompileResult to JSON-serializable dict."""
    return {
        "success": result.success,
        "errors": [_message_to_dict(m) for m in result.errors],
        "warnings": [_message_to_dict(m) for m in result.warnings],
        "timestamp": result.timestamp.isoformat(),
        "duration_seconds": result.duration_seconds,
    }


def _message_to_dict(msg: CompileMessage) -> dict[str, Any]:
    """Convert CompileMessage to dict."""
    d: dict[str, Any] = {
        "file": msg.file,
        "line": msg.line,
        "message": msg.message,
        "type": msg.type,
    }
    if msg.context is not None:
        d["context"] = msg.context
    return d


def _count_source_lines(path: Path) -> int:
    """Count lines in a source file. Returns 0 on failure."""
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# TexWatchServer
# ---------------------------------------------------------------------------


class TexWatchServer:
    """Web server for texwatch.

    Supports two modes:
    - Single-project (legacy): constructed with a Config
    - Multi-project: constructed with a list of (name, Config) pairs
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        projects: list[tuple[str, Config]] | None = None,
    ):
        """Initialize server.

        Provide either ``config`` (single-project, backwards-compatible)
        or ``projects`` (multi-project).
        """
        self.app = web.Application()
        self._projects: dict[str, ProjectInstance] = {}

        if projects is not None:
            for name, cfg in projects:
                self._projects[name] = ProjectInstance(cfg, name=name)
            # Use port from first project or default
            self.config = Config(main="", port=8800)
        elif config is not None:
            self.config = config
            name = Path(config.main).stem if config.main else "default"
            self._projects[name] = ProjectInstance(config, name=name)
        else:
            raise ValueError("Must provide config or projects")

        self._setup_routes()

        # Legacy compatibility: expose state from the single project instance
        # (used by existing tests that access server._compiling etc.)
        self._single: ProjectInstance | None
        if len(self._projects) == 1:
            self._single = next(iter(self._projects.values()))
        else:
            self._single = None

    # --- Legacy property proxies for single-project tests ---

    @property
    def _compiling(self) -> bool:
        if self._single:
            return self._single.compiling
        return any(p.compiling for p in self._projects.values())

    @_compiling.setter
    def _compiling(self, value: bool) -> None:
        if self._single:
            self._single.compiling = value

    @property
    def _last_result(self) -> CompileResult | None:
        if self._single:
            return self._single.last_result
        return None

    @_last_result.setter
    def _last_result(self, value: CompileResult | None) -> None:
        if self._single:
            self._single.last_result = value

    @property
    def _synctex_data(self) -> SyncTeXData | None:
        if self._single:
            return self._single.synctex_data
        return None

    @_synctex_data.setter
    def _synctex_data(self, value: SyncTeXData | None) -> None:
        if self._single:
            self._single.synctex_data = value

    @property
    def _websockets(self) -> set[web.WebSocketResponse]:
        if self._single:
            return self._single.websockets
        # Return union for multi-project
        ws: set[web.WebSocketResponse] = set()
        for p in self._projects.values():
            ws |= p.websockets
        return ws

    @property
    def _viewer_state(self) -> dict[str, Any]:
        if self._single:
            return self._single.viewer_state
        return {"page": 1, "total_pages": 0, "visible_lines": None}

    @_viewer_state.setter
    def _viewer_state(self, value: dict[str, Any]) -> None:
        if self._single:
            self._single.viewer_state = value

    @property
    def _editor_state(self) -> dict[str, Any]:
        if self._single:
            return self._single.editor_state
        return {"file": None, "line": None}

    @_editor_state.setter
    def _editor_state(self, value: dict[str, Any]) -> None:
        if self._single:
            self._single.editor_state = value

    @property
    def _watcher(self) -> TexWatcher | None:
        if self._single:
            return self._single.watcher
        return None

    @_watcher.setter
    def _watcher(self, value: TexWatcher | None) -> None:
        if self._single:
            self._single.watcher = value

    # --- Route setup ---

    def _setup_routes(self):
        """Set up HTTP routes."""
        # Top-level routes
        self.app.router.add_get("/", self._handle_root)
        self.app.router.add_get("/projects", self._handle_projects)

        # Static files
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            self.app.router.add_static("/static/", static_dir, name="static")

        # Per-project routes under /p/{name}/
        self.app.router.add_get("/p/{name}/", self._handle_project_index)
        self.app.router.add_get(r"/p/{name:.+}/ws", self._handle_project_ws)
        self.app.router.add_get(r"/p/{name:.+}/status", self._handle_project_status)
        self.app.router.add_post(r"/p/{name:.+}/goto", self._handle_project_goto)
        self.app.router.add_post(r"/p/{name:.+}/compile", self._handle_project_compile)
        self.app.router.add_get(r"/p/{name:.+}/capture", self._handle_project_capture)
        self.app.router.add_get(r"/p/{name:.+}/config", self._handle_project_config)
        self.app.router.add_get(r"/p/{name:.+}/source", self._handle_project_get_source)
        self.app.router.add_post(r"/p/{name:.+}/source", self._handle_project_post_source)
        self.app.router.add_get(r"/p/{name:.+}/pdf", self._handle_project_pdf)
        self.app.router.add_get(r"/p/{name:.+}/files", self._handle_project_files)
        self.app.router.add_get(r"/p/{name:.+}/errors", self._handle_project_errors)
        self.app.router.add_get(r"/p/{name:.+}/context", self._handle_project_context)
        self.app.router.add_get(r"/p/{name:.+}/structure", self._handle_project_structure)

        # Legacy unprefixed routes (single-project backwards compat)
        self.app.router.add_get("/ws", self._handle_legacy_ws)
        self.app.router.add_get("/status", self._handle_legacy_status)
        self.app.router.add_post("/goto", self._handle_legacy_goto)
        self.app.router.add_post("/compile", self._handle_legacy_compile)
        self.app.router.add_get("/capture", self._handle_legacy_capture)
        self.app.router.add_get("/config", self._handle_legacy_config)
        self.app.router.add_get("/source", self._handle_legacy_get_source)
        self.app.router.add_post("/source", self._handle_legacy_post_source)
        self.app.router.add_get("/pdf", self._handle_legacy_pdf)
        self.app.router.add_get("/files", self._handle_legacy_files)
        self.app.router.add_get("/errors", self._handle_legacy_errors)
        self.app.router.add_get("/context", self._handle_legacy_context)
        self.app.router.add_get("/structure", self._handle_legacy_structure)

    def _get_project(self, request: web.Request) -> ProjectInstance | None:
        """Extract project instance from URL path parameter."""
        name = request.match_info.get("name", "")
        return self._projects.get(name)

    def _get_single_project(self) -> ProjectInstance | None:
        """Get the single project for legacy routes."""
        if self._single:
            return self._single
        # Fallback: if there's exactly one project, use it
        if len(self._projects) == 1:
            return next(iter(self._projects.values()))
        return None

    def _require_project(self, request: web.Request) -> ProjectInstance:
        """Get project from request or raise 404."""
        proj = self._get_project(request)
        if proj is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "Project not found"}),
                                   content_type="application/json")
        return proj

    # --- Top-level routes ---

    async def _handle_root(self, request: web.Request) -> web.Response:
        """Handle GET / — dashboard or redirect to single project."""
        if len(self._projects) == 1:
            # For single-project, serve index.html directly (legacy compat)
            return self._serve_index_html("")
        return self._serve_dashboard_html()

    async def _handle_projects(self, request: web.Request) -> web.Response:
        """Handle GET /projects — JSON list of all projects."""
        summaries = [p.status_summary() for p in self._projects.values()]
        return web.json_response({"projects": summaries})

    # --- Per-project routes ---

    async def _handle_project_index(self, request: web.Request) -> web.Response:
        """Handle GET /p/{name}/ — serve project viewer."""
        proj = self._require_project(request)
        base = f"/p/{proj.name}"
        return self._serve_index_html(base)

    async def _handle_project_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket at /p/{name}/ws."""
        proj = self._require_project(request)
        return await self._handle_websocket(request, proj)

    async def _handle_project_status(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._build_status_response(proj)

    async def _handle_project_goto(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return await self._handle_goto(request, proj)

    async def _handle_project_compile(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return await self._handle_compile(request, proj)

    async def _handle_project_capture(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return await self._handle_capture(request, proj)

    async def _handle_project_config(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return web.json_response(proj.config.to_dict())

    async def _handle_project_get_source(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._handle_get_source(request, proj)

    async def _handle_project_post_source(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return await self._handle_post_source_impl(request, proj)

    async def _handle_project_pdf(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._serve_pdf(proj)

    async def _handle_project_files(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._build_files_response(proj)

    async def _handle_project_errors(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._build_errors_response(proj)

    async def _handle_project_context(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._build_context_response(proj)

    async def _handle_project_structure(self, request: web.Request) -> web.Response:
        proj = self._require_project(request)
        return self._build_structure_response(proj)

    # --- Legacy single-project routes ---

    async def _handle_legacy_ws(self, request: web.Request) -> web.WebSocketResponse:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/ws")
        return await self._handle_websocket(request, proj)

    async def _handle_legacy_status(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            return await self._handle_projects(request)
        return self._build_status_response(proj)

    async def _handle_legacy_goto(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/goto")
        return await self._handle_goto(request, proj)

    async def _handle_legacy_compile(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/compile")
        return await self._handle_compile(request, proj)

    async def _handle_legacy_capture(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/capture")
        return await self._handle_capture(request, proj)

    async def _handle_legacy_config(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/config")
        return web.json_response(proj.config.to_dict())

    async def _handle_legacy_get_source(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/source")
        return self._handle_get_source(request, proj)

    async def _handle_legacy_post_source(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/source")
        return await self._handle_post_source_impl(request, proj)

    async def _handle_legacy_pdf(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/pdf")
        return self._serve_pdf(proj)

    async def _handle_legacy_files(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/files")
        return self._build_files_response(proj)

    async def _handle_legacy_errors(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/errors")
        return self._build_errors_response(proj)

    async def _handle_legacy_context(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/context")
        return self._build_context_response(proj)

    async def _handle_legacy_structure(self, request: web.Request) -> web.Response:
        proj = self._get_single_project()
        if proj is None:
            raise web.HTTPBadRequest(text="Multi-project server: use /p/{name}/structure")
        return self._build_structure_response(proj)

    # --- Shared handler implementations ---

    def _serve_index_html(self, base_url: str) -> web.Response:
        """Serve index.html with TEXWATCH_BASE injected."""
        static_dir = Path(__file__).parent / "static"
        index_path = static_dir / "index.html"

        if not index_path.exists():
            return web.Response(
                text="<html><body><h1>texwatch</h1><p>Static files not found.</p></body></html>",
                content_type="text/html",
            )

        html = index_path.read_text(encoding="utf-8")
        # Inject TEXWATCH_BASE before the closing </head> tag
        script_tag = f'<script>window.TEXWATCH_BASE = {json.dumps(base_url)};</script>'
        html = html.replace("</head>", f"{script_tag}\n</head>", 1)
        return web.Response(text=html, content_type="text/html")

    def _serve_dashboard_html(self) -> web.Response:
        """Serve the multi-project dashboard page."""
        static_dir = Path(__file__).parent / "static"
        dashboard_path = static_dir / "dashboard.html"

        if dashboard_path.exists():
            return web.FileResponse(dashboard_path)  # type: ignore[return-value]

        # Fallback inline dashboard
        project_links = "".join(
            f'<li><a href="/p/{name}/">{name}</a> — {p.config.main}</li>\n'
            for name, p in self._projects.items()
        )
        html = f"""<!DOCTYPE html>
<html><head><title>texwatch dashboard</title>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<header><div class="header-left"><h1>texwatch dashboard</h1></div></header>
<main style="padding:2em"><ul>{project_links}</ul></main>
</body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_websocket(
        self, request: web.Request, proj: ProjectInstance
    ) -> web.WebSocketResponse:
        """Handle WebSocket connection for a project."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        proj.websockets.add(ws)
        logger.info(f"[{proj.name}] WebSocket connected, {len(proj.websockets)} clients")

        await proj.send_state(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(ws, data, proj)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON: {msg.data}")
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            proj.websockets.discard(ws)
            logger.info(f"[{proj.name}] WebSocket disconnected, {len(proj.websockets)} clients")

        return ws

    async def _handle_ws_message(
        self, ws: web.WebSocketResponse, data: dict, proj: ProjectInstance
    ) -> None:
        """Handle incoming WebSocket message."""
        msg_type = data.get("type")

        if msg_type == "viewer_state":
            proj.viewer_state.update(data.get("state", {}))
            if proj.synctex_data:
                page = proj.viewer_state.get("page", 1)
                proj.viewer_state["visible_lines"] = get_visible_lines(proj.synctex_data, page)
            logger.debug(f"[{proj.name}] Viewer state updated: {proj.viewer_state}")

        elif msg_type == "editor_state":
            proj.editor_state.update(data.get("state", {}))
            logger.debug(f"[{proj.name}] Editor state updated: {proj.editor_state}")

        elif msg_type == "click":
            page = data.get("page")
            x = data.get("x")
            y = data.get("y")
            logger.debug(
                "reverse-sync: click page=%s x=%s y=%s, synctex_data=%s",
                page, x, y, "loaded" if proj.synctex_data else "None",
            )
            if proj.synctex_data and page:
                from .synctex import page_to_source
                pos = page_to_source(proj.synctex_data, page, y)
                if pos:
                    logger.debug(
                        "reverse-sync: resolved -> %s:%d (col=%d)",
                        pos.file, pos.line, pos.column,
                    )
                    await ws.send_json({
                        "type": "source_position",
                        "file": pos.file,
                        "line": pos.line,
                        "column": pos.column,
                    })
                else:
                    logger.debug("reverse-sync: page_to_source returned None")
            elif not proj.synctex_data:
                logger.debug("reverse-sync: SKIPPED - no synctex data")

    def _build_status_response(self, proj: ProjectInstance) -> web.Response:
        """Build GET /status response for a project."""
        main_file = get_main_file(proj.config)
        response: dict[str, Any] = {
            "file": str(main_file.name),
            "compiling": proj.compiling,
            "last_compile": (
                proj.last_result.timestamp.isoformat() if proj.last_result else None
            ),
            "success": proj.last_result.success if proj.last_result else None,
            "errors": (
                [_message_to_dict(m) for m in proj.last_result.errors]
                if proj.last_result
                else []
            ),
            "warnings": (
                [_message_to_dict(m) for m in proj.last_result.warnings]
                if proj.last_result
                else []
            ),
            "page_limit": proj.config.page_limit,
            "total_pages": proj.viewer_state.get("total_pages", 0),
            "viewer": proj.viewer_state,
            "editor": proj.editor_state,
        }
        return web.json_response(response)

    def _build_errors_response(self, proj: ProjectInstance) -> web.Response:
        """Build GET /errors response for a project."""
        if proj.last_result is None:
            return web.json_response({"errors": [], "warnings": []})

        return web.json_response({
            "errors": [_message_to_dict(m) for m in proj.last_result.errors],
            "warnings": [_message_to_dict(m) for m in proj.last_result.warnings],
        })

    def _build_context_response(self, proj: ProjectInstance) -> web.Response:
        """Build GET /context response for a project.

        Combines viewer state, editor state, compile status, and
        document structure into a single snapshot.
        """
        main_file = get_main_file(proj.config)
        watch_dir = get_watch_dir(proj.config)

        # Parse document structure (sections, todos, inputs, word count)
        try:
            structure = parse_structure(main_file, watch_dir)
        except Exception:
            logger.debug("context: parse_structure failed", exc_info=True)
            structure = None

        # Determine current_section from editor state
        current_section: str | None = None
        if structure and structure.sections:
            editor_file = proj.editor_state.get("file")
            editor_line = proj.editor_state.get("line")
            if editor_file and editor_line is not None:
                # Find the section heading closest to (and before) the cursor
                best: str | None = None
                best_line = -1
                for sec in structure.sections:
                    if sec.file == editor_file and sec.line <= editor_line:
                        if sec.line > best_line:
                            best = sec.title
                            best_line = sec.line
                current_section = best

        errors_count = len(proj.last_result.errors) if proj.last_result else 0
        warnings_count = len(proj.last_result.warnings) if proj.last_result else 0

        response: dict[str, Any] = {
            "editor": proj.editor_state,
            "viewer": proj.viewer_state,
            "compiling": proj.compiling,
            "errors_count": errors_count,
            "warnings_count": warnings_count,
            "current_section": current_section,
            "page_limit": proj.config.page_limit,
            "word_count": structure.word_count if structure else None,
        }
        return web.json_response(response)

    def _build_structure_response(self, proj: ProjectInstance) -> web.Response:
        """Build GET /structure response for a project.

        Returns the full document structure: sections, TODOs, inputs, and
        word count.
        """
        main_file = get_main_file(proj.config)
        watch_dir = get_watch_dir(proj.config)

        try:
            structure = parse_structure(main_file, watch_dir)
        except Exception:
            logger.debug("structure endpoint: parse_structure failed", exc_info=True)
            return web.json_response({
                "sections": [],
                "todos": [],
                "inputs": [],
                "word_count": None,
            })

        response: dict[str, Any] = {
            "sections": [
                {"level": s.level, "title": s.title, "file": s.file, "line": s.line}
                for s in structure.sections
            ],
            "todos": [
                {"text": t.text, "file": t.file, "line": t.line, "tag": t.tag}
                for t in structure.todos
            ],
            "inputs": [
                {"path": i.path, "file": i.file, "line": i.line}
                for i in structure.inputs
            ],
            "word_count": structure.word_count,
        }
        return web.json_response(response)

    async def _handle_goto(self, request: web.Request, proj: ProjectInstance) -> web.Response:
        """Handle POST /goto for a project."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if "line" in data:
            main_file = get_main_file(proj.config)
            if main_file.suffix.lower() not in (".tex",):
                return web.json_response(
                    {"error": "SyncTeX not available for this file type"}, status=501
                )
            line = data["line"]
            file_name = data.get("file") or proj.editor_state.get("file") or str(main_file.name)
            logger.debug(
                "goto: line=%d, file=%s, synctex_data=%s",
                line, file_name, "loaded" if proj.synctex_data else "None",
            )
            if proj.synctex_data:
                pos = source_to_page(proj.synctex_data, file_name, line)
                if pos:
                    logger.debug(
                        "goto: synctex hit -> broadcasting page=%d x=%.1f y=%.1f w=%.1f h=%.1f",
                        pos.page, pos.x, pos.y, pos.width, pos.height,
                    )
                    await proj.broadcast({
                        "type": "goto",
                        "page": pos.page,
                        "x": pos.x,
                        "y": pos.y,
                        "width": pos.width,
                        "height": pos.height,
                    })
                    return web.json_response({"success": True, "page": pos.page})

            logger.debug("goto: synctex miss for line=%d, falling back to page estimation", line)
            total_pages = proj.viewer_state.get("total_pages", 0)
            if total_pages > 0:
                total_lines = _count_source_lines(main_file)
                if total_lines > 0:
                    estimated_page = round(line / total_lines * total_pages)
                    estimated_page = max(1, min(estimated_page, total_pages))
                else:
                    estimated_page = max(1, min(line, total_pages))
                await proj.broadcast({"type": "goto", "page": estimated_page})
                return web.json_response({
                    "success": True,
                    "page": estimated_page,
                    "estimated": True,
                })

            await proj.broadcast({"type": "goto", "page": 1})
            return web.json_response({"success": True, "estimated": True})

        if "page" in data:
            page = data["page"]
            await proj.broadcast({"type": "goto", "page": page})
            return web.json_response({"success": True, "page": page})

        if "section" in data:
            query = data["section"]
            main_file = get_main_file(proj.config)
            watch_dir = get_watch_dir(proj.config)

            try:
                structure = parse_structure(main_file, watch_dir)
            except Exception:
                logger.debug("goto section: parse_structure failed", exc_info=True)
                return web.json_response(
                    {"error": "Failed to parse document structure"}, status=500
                )

            # Find matching section: case-insensitive substring, prefer exact match
            query_lower = query.lower()
            candidates = [
                s for s in structure.sections
                if query_lower in s.title.lower()
            ]

            if not candidates:
                available = [s.title for s in structure.sections]
                return web.json_response(
                    {
                        "error": f"No section matching '{query}' found",
                        "available_sections": available,
                    },
                    status=404,
                )

            # Prefer exact (case-insensitive) match over substring
            exact = [s for s in candidates if s.title.lower() == query_lower]
            matched = exact[0] if exact else candidates[0]

            logger.debug(
                "goto section: matched '%s' in %s:%d",
                matched.title, matched.file, matched.line,
            )

            # Forward sync via SyncTeX
            if proj.synctex_data:
                pos = source_to_page(proj.synctex_data, matched.file, matched.line)
                if pos:
                    logger.debug(
                        "goto section: synctex hit -> page=%d x=%.1f y=%.1f",
                        pos.page, pos.x, pos.y,
                    )
                    await proj.broadcast({
                        "type": "goto",
                        "page": pos.page,
                        "x": pos.x,
                        "y": pos.y,
                        "width": pos.width,
                        "height": pos.height,
                    })
                    return web.json_response({
                        "success": True,
                        "page": pos.page,
                        "section": matched.title,
                    })

            # Fallback: estimate page from line position
            logger.debug("goto section: synctex miss, falling back to page estimation")
            total_pages = proj.viewer_state.get("total_pages", 0)
            if total_pages > 0:
                total_lines = _count_source_lines(main_file)
                if total_lines > 0:
                    estimated_page = round(matched.line / total_lines * total_pages)
                    estimated_page = max(1, min(estimated_page, total_pages))
                else:
                    estimated_page = max(1, min(matched.line, total_pages))
                await proj.broadcast({"type": "goto", "page": estimated_page})
                return web.json_response({
                    "success": True,
                    "page": estimated_page,
                    "section": matched.title,
                    "estimated": True,
                })

            await proj.broadcast({"type": "goto", "page": 1})
            return web.json_response({
                "success": True,
                "section": matched.title,
                "estimated": True,
            })

        return web.json_response({"error": "Must specify line, page, or section"}, status=400)

    async def _handle_compile(self, request: web.Request, proj: ProjectInstance) -> web.Response:
        """Handle POST /compile for a project."""
        if proj.compiling:
            return web.json_response({"error": "Compilation already in progress"}, status=409)

        await proj.do_compile()

        if proj.last_result:
            return web.json_response(_result_to_dict(proj.last_result))
        else:
            return web.json_response({"error": "Compilation failed"}, status=500)

    async def _handle_capture(self, request: web.Request, proj: ProjectInstance) -> web.Response:
        """Handle GET /capture for a project."""
        try:
            import pymupdf
        except ImportError:
            return web.json_response(
                {"error": "pymupdf not installed. Run: pip install texwatch[capture]"},
                status=501,
            )

        main_file = get_main_file(proj.config)
        pdf_path = main_file.with_suffix(".pdf")

        if not pdf_path.exists():
            return web.json_response({"error": "PDF not found"}, status=404)

        page_param = request.query.get("page")
        dpi_param = request.query.get("dpi")

        try:
            dpi = int(dpi_param) if dpi_param else 150
        except ValueError:
            return web.json_response(
                {"error": f"Invalid dpi value: {dpi_param}"}, status=400
            )
        dpi = max(72, min(dpi, 600))

        try:
            doc = pymupdf.open(str(pdf_path))
        except Exception as e:
            return web.json_response(
                {"error": f"Failed to open PDF: {e}"}, status=500
            )

        try:
            total = len(doc)
            if total == 0:
                return web.json_response({"error": "PDF has no pages"}, status=400)

            if page_param is not None:
                try:
                    page_num = int(page_param)
                except ValueError:
                    return web.json_response(
                        {"error": f"Invalid page value: {page_param}"}, status=400
                    )
                if page_num < 1 or page_num > total:
                    return web.json_response(
                        {"error": f"Page {page_num} out of range (1-{total})"},
                        status=400,
                    )
            else:
                page_num = proj.viewer_state.get("page", 1)
                page_num = max(1, min(page_num, total))

            page = doc[page_num - 1]
            zoom = dpi / 72.0
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            png_data = pix.tobytes("png")
        finally:
            doc.close()

        return web.Response(body=png_data, content_type="image/png")

    def _handle_get_source(self, request: web.Request, proj: ProjectInstance) -> web.Response:
        """Handle GET /source for a project."""
        file_param = request.query.get("file")
        if not file_param:
            file_param = get_main_file(proj.config).name

        watch_dir = get_watch_dir(proj.config)
        file_path = watch_dir / file_param

        # Check for symlinks before resolving to prevent path traversal
        if file_path.is_symlink():
            return web.json_response({"error": "Symlinks not allowed"}, status=403)

        file_path = file_path.resolve()
        if not file_path.is_relative_to(watch_dir.resolve()):
            return web.json_response({"error": "Access denied"}, status=403)

        if not file_path.exists():
            return web.json_response({"error": "File not found"}, status=404)

        content = file_path.read_text(encoding="utf-8", errors="replace")
        mtime_ns = str(file_path.stat().st_mtime_ns)
        return web.json_response({"file": file_param, "content": content, "mtime_ns": mtime_ns})

    async def _handle_post_source_impl(
        self, request: web.Request, proj: ProjectInstance
    ) -> web.Response:
        """Handle POST /source for a project."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        file_param = data.get("file")
        content = data.get("content")

        if not file_param or content is None:
            return web.json_response(
                {"error": "Missing required fields: file, content"}, status=400
            )

        watch_dir = get_watch_dir(proj.config)
        file_path = watch_dir / file_param

        # Check for symlinks before resolving to prevent path traversal
        if file_path.is_symlink():
            return web.json_response({"error": "Symlinks not allowed"}, status=403)

        file_path = file_path.resolve()
        if not file_path.is_relative_to(watch_dir.resolve()):
            return web.json_response({"error": "Access denied"}, status=403)

        if not file_path.exists():
            return web.json_response({"error": "File not found"}, status=404)

        base_mtime_ns = data.get("base_mtime_ns")
        if base_mtime_ns is not None:
            current_mtime_ns = str(file_path.stat().st_mtime_ns)
            if current_mtime_ns != base_mtime_ns:
                return web.json_response(
                    {"error": "File modified externally", "current_mtime_ns": current_mtime_ns},
                    status=409,
                )

        file_path.write_text(content, encoding="utf-8")
        new_mtime_ns = str(file_path.stat().st_mtime_ns)
        return web.json_response({"success": True, "mtime_ns": new_mtime_ns})

    def _serve_pdf(self, proj: ProjectInstance) -> web.Response:
        """Serve the compiled PDF for a project."""
        main_file = get_main_file(proj.config)
        pdf_path = main_file.with_suffix(".pdf")

        if pdf_path.exists():
            # FileResponse is a StreamResponse subclass, but type stubs are imprecise
            return web.FileResponse(  # type: ignore[return-value]
                pdf_path,
                headers={"Content-Type": "application/pdf"},
            )
        else:
            return web.json_response({"error": "PDF not found"}, status=404)

    def _build_files_response(self, proj: ProjectInstance) -> web.Response:
        """Build GET /files response for a project."""
        watch_dir = get_watch_dir(proj.config)
        tree = self._build_file_tree(watch_dir, watch_dir)
        return web.json_response({"root": watch_dir.name, "children": tree})

    def _build_file_tree(self, directory: Path, base_dir: Path) -> list[dict]:
        """Build a recursive file tree of relevant project files."""
        RELEVANT_EXTENSIONS = {
            ".tex", ".md", ".txt", ".bib", ".cls", ".sty",
            ".bst", ".dtx", ".tikz", ".lua",
        }
        SKIP_DIRS = {"_build", "build", "out", "__pycache__"}

        entries: list[dict] = []

        try:
            items = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return entries

        for item in items:
            if item.name.startswith("."):
                continue

            if item.is_dir():
                if item.name in SKIP_DIRS:
                    continue
                if item.is_symlink():
                    try:
                        resolved = item.resolve()
                        if not resolved.is_relative_to(base_dir.resolve()):
                            continue
                    except (OSError, ValueError):
                        continue
                children = self._build_file_tree(item, base_dir)
                if children:
                    rel_path = str(item.relative_to(base_dir))
                    entries.append({
                        "name": item.name,
                        "path": rel_path,
                        "type": "directory",
                        "children": children,
                    })
            elif item.is_file():
                if item.is_symlink():
                    try:
                        resolved = item.resolve()
                        if not resolved.is_relative_to(base_dir.resolve()):
                            continue
                    except (OSError, ValueError):
                        continue
                if item.suffix.lower() in RELEVANT_EXTENSIONS:
                    rel_path = str(item.relative_to(base_dir))
                    entries.append({
                        "name": item.name,
                        "path": rel_path,
                        "type": "file",
                    })

        return entries

    # --- Legacy helper methods (kept for test compat) ---

    def _result_to_dict(self, result: CompileResult) -> dict[str, Any]:
        return _result_to_dict(result)

    def _message_to_dict(self, msg: CompileMessage) -> dict[str, Any]:
        return _message_to_dict(msg)

    def _count_source_lines(self, path: Path) -> int:
        return _count_source_lines(path)

    async def _send_state(self, ws: web.WebSocketResponse) -> None:
        """Legacy: send state from single project."""
        if self._single:
            await self._single.send_state(ws)

    async def _broadcast(self, message: dict) -> None:
        """Legacy: broadcast to single project."""
        if self._single:
            await self._single.broadcast(message)

    async def _do_compile(self) -> None:
        """Legacy: compile single project."""
        if self._single:
            await self._single.do_compile()

    async def _on_file_change(self, changed_path: str) -> None:
        """Legacy: handle file change for single project."""
        if self._single:
            await self._single.on_file_change(changed_path)

    # --- Server lifecycle ---

    async def start(self) -> None:
        """Start watchers and initial compile for all projects."""
        loop = asyncio.get_event_loop()

        for name, proj in self._projects.items():
            watch_dir = get_watch_dir(proj.config)
            proj.watcher = TexWatcher(
                watch_dir=watch_dir,
                watch_patterns=proj.config.watch,
                ignore_patterns=proj.config.ignore,
                on_change=proj.on_file_change,
            )
            proj.watcher.start(loop)
            await proj.do_compile()

    async def stop(self) -> None:
        """Stop all watchers and close all WebSocket connections."""
        for proj in self._projects.values():
            if proj.watcher:
                proj.watcher.stop()
            for ws in list(proj.websockets):
                await ws.close()

    def run(self, host: str = "localhost", port: int | None = None) -> None:
        """Run the server (blocking)."""
        if port is None:
            port = self.config.port

        async def runner():
            await self.start()
            app_runner = web.AppRunner(self.app)
            await app_runner.setup()
            site = web.TCPSite(app_runner, host, port)
            await site.start()

            if len(self._projects) == 1:
                name = next(iter(self._projects))
                proj = self._projects[name]
                print(f"texwatch running at http://{host}:{port}")
                print(f"Watching: {get_watch_dir(proj.config)}")
            else:
                print(f"texwatch serving {len(self._projects)} projects at http://{host}:{port}")
                for name, proj in self._projects.items():
                    print(f"  {name}: {get_watch_dir(proj.config)} ({proj.config.main})")
            print("Press Ctrl+C to stop")

            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                await self.stop()
                await app_runner.cleanup()

        try:
            asyncio.run(runner())
        except KeyboardInterrupt:
            print("\nStopping texwatch...")
        except OSError as e:
            import errno as _errno
            if e.errno == _errno.EADDRINUSE:
                print(f"\nError: Port {port} is already in use.")
                print("Is another texwatch instance running?")
                print(f"Try a different port: texwatch --port {port + 1}")
                raise SystemExit(1) from None
            raise

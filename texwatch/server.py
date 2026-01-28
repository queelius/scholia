"""aiohttp web server with HTTP API and WebSocket support."""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType

from .compiler import CompileMessage, CompileResult, compile_tex
from .config import Config, get_main_file, get_watch_dir
from .synctex import (
    PDFPosition,
    SyncTeXData,
    find_synctex_file,
    get_visible_lines,
    parse_synctex,
    source_to_page,
)
from .watcher import TexWatcher

logger = logging.getLogger(__name__)


class TexWatchServer:
    """Web server for texwatch."""

    def __init__(self, config: Config):
        """Initialize server.

        Args:
            config: texwatch configuration.
        """
        self.config = config
        self.app = web.Application()
        self._setup_routes()

        # State
        self._compiling = False
        self._last_result: CompileResult | None = None
        self._synctex_data: SyncTeXData | None = None
        self._websockets: set[web.WebSocketResponse] = set()
        self._watcher: TexWatcher | None = None
        self._viewer_state = {
            "page": 1,
            "total_pages": 0,
            "visible_lines": None,
        }

    def _setup_routes(self):
        """Set up HTTP routes."""
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/ws", self._handle_websocket)
        self.app.router.add_get("/status", self._handle_status)
        self.app.router.add_post("/goto", self._handle_goto)
        self.app.router.add_post("/compile", self._handle_compile)
        self.app.router.add_get("/capture", self._handle_capture)
        self.app.router.add_get("/config", self._handle_config)
        self.app.router.add_get("/pdf", self._handle_pdf)

        # Serve static files
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            self.app.router.add_static("/static/", static_dir, name="static")

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the main HTML page."""
        static_dir = Path(__file__).parent / "static"
        index_path = static_dir / "index.html"

        if index_path.exists():
            return web.FileResponse(index_path)
        else:
            return web.Response(
                text="<html><body><h1>texwatch</h1><p>Static files not found.</p></body></html>",
                content_type="text/html",
            )

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._websockets.add(ws)
        logger.info(f"WebSocket connected, {len(self._websockets)} clients")

        # Send initial state
        await self._send_state(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(ws, data)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON: {msg.data}")
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._websockets.discard(ws)
            logger.info(f"WebSocket disconnected, {len(self._websockets)} clients")

        return ws

    async def _handle_ws_message(self, ws: web.WebSocketResponse, data: dict) -> None:
        """Handle incoming WebSocket message."""
        msg_type = data.get("type")

        if msg_type == "viewer_state":
            # Update viewer state from browser
            self._viewer_state.update(data.get("state", {}))
            logger.debug(f"Viewer state updated: {self._viewer_state}")

        elif msg_type == "click":
            # PDF click - return source position via SyncTeX
            page = data.get("page")
            y = data.get("y")
            if self._synctex_data and page:
                from .synctex import page_to_source
                pos = page_to_source(self._synctex_data, page, y)
                if pos:
                    await ws.send_json({
                        "type": "source_position",
                        "file": pos.file,
                        "line": pos.line,
                        "column": pos.column,
                    })

    async def _send_state(self, ws: web.WebSocketResponse) -> None:
        """Send current state to a WebSocket client."""
        await ws.send_json({
            "type": "state",
            "compiling": self._compiling,
            "result": self._result_to_dict(self._last_result) if self._last_result else None,
            "viewer": self._viewer_state,
        })

    async def _broadcast(self, message: dict) -> None:
        """Broadcast message to all WebSocket clients."""
        if not self._websockets:
            return

        for ws in list(self._websockets):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                self._websockets.discard(ws)

    def _result_to_dict(self, result: CompileResult) -> dict[str, Any]:
        """Convert CompileResult to JSON-serializable dict."""
        return {
            "success": result.success,
            "errors": [self._message_to_dict(m) for m in result.errors],
            "warnings": [self._message_to_dict(m) for m in result.warnings],
            "timestamp": result.timestamp.isoformat(),
            "duration_seconds": result.duration_seconds,
        }

    def _message_to_dict(self, msg: CompileMessage) -> dict[str, Any]:
        """Convert CompileMessage to dict."""
        return {
            "file": msg.file,
            "line": msg.line,
            "message": msg.message,
            "type": msg.type,
        }

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Handle GET /status."""
        main_file = get_main_file(self.config)

        response = {
            "file": str(main_file.name),
            "compiling": self._compiling,
            "last_compile": (
                self._last_result.timestamp.isoformat() if self._last_result else None
            ),
            "success": self._last_result.success if self._last_result else None,
            "errors": (
                [self._message_to_dict(m) for m in self._last_result.errors]
                if self._last_result
                else []
            ),
            "warnings": (
                [self._message_to_dict(m) for m in self._last_result.warnings]
                if self._last_result
                else []
            ),
            "viewer": self._viewer_state,
        }

        return web.json_response(response)

    async def _handle_goto(self, request: web.Request) -> web.Response:
        """Handle POST /goto."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Handle line-based navigation
        if "line" in data:
            line = data["line"]
            if self._synctex_data:
                main_file = get_main_file(self.config)
                pos = source_to_page(self._synctex_data, str(main_file.name), line)
                if pos:
                    await self._broadcast({
                        "type": "goto",
                        "page": pos.page,
                        "y": pos.y,
                    })
                    return web.json_response({"success": True, "page": pos.page})

            return web.json_response({"error": "Line not found in PDF"}, status=404)

        # Handle page-based navigation
        if "page" in data:
            page = data["page"]
            await self._broadcast({
                "type": "goto",
                "page": page,
            })
            return web.json_response({"success": True, "page": page})

        # Handle section-based navigation (search in PDF)
        if "section" in data:
            # This would require PDF text search - for now, return not implemented
            return web.json_response({"error": "Section search not implemented"}, status=501)

        return web.json_response({"error": "Must specify line, page, or section"}, status=400)

    async def _handle_compile(self, request: web.Request) -> web.Response:
        """Handle POST /compile."""
        if self._compiling:
            return web.json_response({"error": "Compilation already in progress"}, status=409)

        await self._do_compile()

        if self._last_result:
            return web.json_response(self._result_to_dict(self._last_result))
        else:
            return web.json_response({"error": "Compilation failed"}, status=500)

    async def _handle_capture(self, request: web.Request) -> web.Response:
        """Handle GET /capture - screenshot current view."""
        # This requires browser-side implementation
        # For now, return the PDF directly as a fallback
        main_file = get_main_file(self.config)
        pdf_path = main_file.with_suffix(".pdf")

        if pdf_path.exists():
            return web.json_response({
                "error": "Capture requires browser-side implementation",
                "pdf_url": "/pdf",
            }, status=501)
        else:
            return web.json_response({"error": "PDF not found"}, status=404)

    async def _handle_config(self, request: web.Request) -> web.Response:
        """Handle GET /config."""
        return web.json_response(self.config.to_dict())

    async def _handle_pdf(self, request: web.Request) -> web.Response:
        """Serve the compiled PDF."""
        main_file = get_main_file(self.config)
        pdf_path = main_file.with_suffix(".pdf")

        if pdf_path.exists():
            return web.FileResponse(
                pdf_path,
                headers={"Content-Type": "application/pdf"},
            )
        else:
            return web.json_response({"error": "PDF not found"}, status=404)

    async def _do_compile(self) -> None:
        """Run compilation."""
        self._compiling = True
        await self._broadcast({"type": "compiling", "status": True})

        try:
            main_file = get_main_file(self.config)
            work_dir = get_watch_dir(self.config)

            self._last_result = await compile_tex(
                main_file=main_file,
                compiler=self.config.compiler,
                work_dir=work_dir,
            )

            # Reload SyncTeX data
            if self._last_result.output_file:
                synctex_path = find_synctex_file(self._last_result.output_file)
                if synctex_path:
                    self._synctex_data = parse_synctex(synctex_path)

                # Update total pages (would need PDF parsing - simplified here)
                self._viewer_state["total_pages"] = 0  # Would be set by browser

            logger.info(
                f"Compilation {'succeeded' if self._last_result.success else 'failed'} "
                f"in {self._last_result.duration_seconds:.2f}s"
            )

        finally:
            self._compiling = False
            await self._broadcast({
                "type": "compiling",
                "status": False,
            })
            await self._broadcast({
                "type": "compiled",
                "result": self._result_to_dict(self._last_result) if self._last_result else None,
            })

    async def _on_file_change(self) -> None:
        """Handle file change from watcher."""
        logger.info("File change detected, recompiling...")
        await self._do_compile()

    async def start(self) -> None:
        """Start the server and file watcher."""
        loop = asyncio.get_event_loop()

        # Start file watcher
        watch_dir = get_watch_dir(self.config)
        self._watcher = TexWatcher(
            watch_dir=watch_dir,
            watch_patterns=self.config.watch,
            ignore_patterns=self.config.ignore,
            on_change=self._on_file_change,
        )
        self._watcher.start(loop)

        # Do initial compile
        await self._do_compile()

    async def stop(self) -> None:
        """Stop the server and file watcher."""
        if self._watcher:
            self._watcher.stop()

        # Close all WebSocket connections
        for ws in list(self._websockets):
            await ws.close()

    def run(self, host: str = "localhost", port: int | None = None) -> None:
        """Run the server (blocking)."""
        if port is None:
            port = self.config.port

        async def runner():
            await self.start()
            runner = web.AppRunner(self.app)
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()

            print(f"texwatch running at http://{host}:{port}")
            print(f"Watching: {get_watch_dir(self.config)}")
            print("Press Ctrl+C to stop")

            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                await self.stop()
                await runner.cleanup()

        try:
            asyncio.run(runner())
        except KeyboardInterrupt:
            print("\nStopping texwatch...")

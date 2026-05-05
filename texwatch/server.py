"""aiohttp web server for texwatch v0.4.0.

Single paper, no workspace abstraction.  Three responsibilities:

1. Watch the project directory and recompile on .tex/.bib changes.
2. Serve the resulting PDF + a thin viewer with an annotation layer.
3. Expose a JSON API for comments, paper state, errors, and SyncTeX
   resolution (used by the browser viewer and the MCP server).

There is no editor.  The human writes comments; Claude Code edits files
through its own tools (Edit/Write) and the watcher picks up the changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .comments import (
    Comment,
    CommentStore,
    PaperAnchor,
    PdfRegionAnchor,
    ResolvedSource,
    SectionAnchor,
    SourceRangeAnchor,
    anchor_from_dict,
    capture_snippet,
)
from .compiler import CompileResult, compile_tex
from .config import Config, get_main_file, get_watch_dir
from .structure import (
    DocumentStructure,
    find_section,
    parse_structure,
)
from .synctex import (
    SyncTeXData,
    find_synctex_file,
    page_to_source,
    parse_synctex,
    source_to_page,
)
from .watcher import TexWatcher

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result_to_dict(r: CompileResult | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "success": r.success,
        "errors": [dataclasses.asdict(e) for e in r.errors],
        "warnings": [dataclasses.asdict(w) for w in r.warnings],
        "output_file": str(r.output_file) if r.output_file else None,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "duration_seconds": r.duration_seconds,
    }


def _comment_to_dict(c: Comment) -> dict[str, Any]:
    return c.to_dict()


def _section_resolver_factory(structure: DocumentStructure, watch_dir: Path):
    """Create a sections_resolver for CommentStore.check_staleness."""

    def resolver(title: str, label: str | None) -> ResolvedSource | None:
        match = find_section(structure, title=title, label=label)
        if match is None:
            return None
        file, line_start, line_end = match
        if line_end < 0:
            # "to EOF" — read file to find actual EOF line
            path = watch_dir / file
            try:
                line_end = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                line_end = line_start
        return ResolvedSource(file=file, line_start=line_start, line_end=line_end)

    return resolver


def _resolve_pdf_region_to_source(
    synctex: SyncTeXData | None,
    page: int,
    bbox: tuple[float, float, float, float],
) -> ResolvedSource | None:
    """Use SyncTeX to map a PDF bbox center to a source range."""
    if synctex is None:
        return None
    # Use bbox centroid y for resolution
    _, y1, _, y2 = bbox
    y_center = (y1 + y2) / 2
    src = page_to_source(synctex, page, y_center)
    if src is None:
        return None
    return ResolvedSource(file=src.file, line_start=src.line, line_end=src.line)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class TexWatchServer:
    """Single-paper watch + serve + comment store."""

    def __init__(self, config: Config):
        self.config = config
        self.watch_dir = get_watch_dir(config)
        self.main_file = get_main_file(config)

        self.last_result: CompileResult | None = None
        self.synctex_data: SyncTeXData | None = None
        self.structure: DocumentStructure | None = None
        self.compiling = False

        self.comments = CommentStore(self.watch_dir / ".texwatch" / "comments.json")
        self.websockets: set[web.WebSocketResponse] = set()
        self.watcher: TexWatcher | None = None

        self.app = self._build_app()

    # ----- routes -----

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_get("/", self._handle_root)
        app.router.add_static("/static/", STATIC_DIR, name="static")
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/pdf", self._handle_pdf)
        app.router.add_get("/errors", self._handle_errors)
        app.router.add_get("/paper", self._handle_paper)
        app.router.add_post("/compile", self._handle_compile)
        app.router.add_get("/comments", self._handle_list_comments)
        app.router.add_post("/comments", self._handle_create_comment)
        app.router.add_get(r"/comments/{id}", self._handle_get_comment)
        app.router.add_post(r"/comments/{id}/reply", self._handle_reply_comment)
        app.router.add_post(r"/comments/{id}/resolve", self._handle_resolve_comment)
        app.router.add_post(r"/comments/{id}/dismiss", self._handle_dismiss_comment)
        app.router.add_post(r"/comments/{id}/reopen", self._handle_reopen_comment)
        app.router.add_delete(r"/comments/{id}", self._handle_delete_comment)
        app.router.add_get("/synctex/source-to-pdf", self._handle_synctex_forward)
        app.router.add_get("/synctex/pdf-to-source", self._handle_synctex_reverse)
        app.router.add_post("/goto", self._handle_goto)
        return app

    # ----- compile + watch -----

    async def do_compile(self) -> CompileResult:
        self.compiling = True
        await self.broadcast({"type": "compiling", "status": True})
        try:
            self.last_result = await compile_tex(
                main_file=self.main_file,
                compiler=self.config.compiler,
                work_dir=self.watch_dir,
            )
            # Reload SyncTeX
            if self.last_result.output_file and self.main_file.suffix.lower() == ".tex":
                synctex_path = find_synctex_file(self.last_result.output_file)
                if synctex_path:
                    self.synctex_data = parse_synctex(synctex_path)
            # Refresh structure
            self.structure = parse_structure(self.watch_dir)
            # Re-check staleness of open comments
            if self.structure:
                self.comments.check_staleness(
                    self.watch_dir,
                    sections_resolver=_section_resolver_factory(
                        self.structure, self.watch_dir
                    ),
                )
            logger.info(
                "Compile %s in %.2fs",
                "succeeded" if self.last_result.success else "failed",
                self.last_result.duration_seconds,
            )
        finally:
            self.compiling = False
            await self.broadcast({"type": "compiling", "status": False})
            await self.broadcast(
                {"type": "compiled", "result": _result_to_dict(self.last_result)}
            )
        return self.last_result

    async def on_file_change(self, changed_path: str) -> None:
        logger.info("File change (%s); recompiling…", changed_path)
        await self.do_compile()

    # ----- websocket -----

    async def broadcast(self, msg: dict) -> None:
        if not self.websockets:
            return
        for ws in list(self.websockets):
            try:
                await ws.send_json(msg)
            except Exception:
                self.websockets.discard(ws)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.websockets.add(ws)
        # Send initial state
        await ws.send_json(
            {
                "type": "state",
                "compiling": self.compiling,
                "result": _result_to_dict(self.last_result),
            }
        )
        try:
            async for raw in ws:
                if raw.type == WSMsgType.TEXT:
                    try:
                        msg = json.loads(raw.data)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") == "ping":
                        await ws.send_json({"type": "pong"})
        finally:
            self.websockets.discard(ws)
        return ws

    # ----- static / PDF -----

    async def _handle_root(self, request: web.Request) -> web.Response:
        index = STATIC_DIR / "index.html"
        return web.FileResponse(index)

    async def _handle_pdf(self, request: web.Request) -> web.StreamResponse:
        if self.last_result is None or self.last_result.output_file is None:
            return web.json_response(
                {"error": "no PDF available"}, status=404
            )
        return web.FileResponse(
            self.last_result.output_file,
            headers={"Cache-Control": "no-store"},
        )

    # ----- API: errors / paper / compile -----

    async def _handle_errors(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "success": self.last_result.success if self.last_result else None,
                "errors": [dataclasses.asdict(e) for e in (self.last_result.errors if self.last_result else [])],
                "warnings": [dataclasses.asdict(w) for w in (self.last_result.warnings if self.last_result else [])],
            }
        )

    async def _handle_paper(self, request: web.Request) -> web.Response:
        if self.structure is None:
            self.structure = parse_structure(self.watch_dir)

        # Compute end_line for each section
        sections_with_ends: list[dict[str, Any]] = []
        for s in self.structure.sections:
            match = find_section(self.structure, title=s.title, label=s.label)
            line_end = match[2] if match else -1
            if line_end < 0:
                # to EOF
                try:
                    abs_path = self.watch_dir / s.file
                    line_end = len(
                        abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    )
                except OSError:
                    line_end = s.line
            sections_with_ends.append(
                {
                    "level": s.level,
                    "title": s.title,
                    "file": s.file,
                    "line": s.line,
                    "line_end": line_end,
                    "label": s.label,
                }
            )

        comment_summary = {
            "open": len(self.comments.list(status="open")),
            "resolved": len(self.comments.list(status="resolved")),
            "dismissed": len(self.comments.list(status="dismissed")),
            "stale": sum(1 for c in self.comments.list(status="open") if c.stale),
        }

        return web.json_response(
            {
                "main_file": self.config.main,
                "watch_dir": str(self.watch_dir),
                "compiling": self.compiling,
                "last_compile": _result_to_dict(self.last_result),
                "sections": sections_with_ends,
                "labels": [
                    {"name": l.name, "file": l.file, "line": l.line}
                    for l in self.structure.labels
                ],
                "citations": [
                    {"key": c.key, "file": c.file, "line": c.line}
                    for c in self.structure.citations
                ],
                "inputs": [
                    {"path": i.path, "file": i.file, "line": i.line}
                    for i in self.structure.inputs
                ],
                "comments": comment_summary,
            }
        )

    async def _handle_compile(self, request: web.Request) -> web.Response:
        result = await self.do_compile()
        return web.json_response(_result_to_dict(result))

    # ----- API: comments -----

    async def _handle_list_comments(self, request: web.Request) -> web.Response:
        status = request.query.get("status")
        tags = request.query.getall("tag", [])
        kwargs: dict[str, Any] = {}
        if status in ("open", "resolved", "dismissed"):
            kwargs["status"] = status
        if tags:
            kwargs["tags"] = tags
        comments = self.comments.list(**kwargs)
        return web.json_response(
            {"comments": [_comment_to_dict(c) for c in comments]}
        )

    async def _handle_get_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        c = self.comments.get(cid)
        if c is None:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        return web.json_response(_comment_to_dict(c))

    async def _handle_create_comment(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        anchor_d = data.get("anchor")
        text = data.get("text", "").strip()
        author = data.get("author", "human")
        tags = data.get("tags") or []

        if not anchor_d or not text:
            return web.json_response(
                {"error": "anchor and text are required"}, status=400
            )
        try:
            anchor = anchor_from_dict(anchor_d)
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response({"error": f"invalid anchor: {exc}"}, status=400)

        # Resolve to source location and capture snippet
        resolved, snippet = self._resolve_anchor(anchor)

        comment = self.comments.add(
            anchor=anchor,
            text=text,
            author=author,
            tags=tags,
            resolved_source=resolved,
            snippet=snippet,
        )
        await self.broadcast({"type": "comment_added", "comment": _comment_to_dict(comment)})
        return web.json_response(_comment_to_dict(comment), status=201)

    def _resolve_anchor(self, anchor) -> tuple[ResolvedSource | None, str | None]:
        """Resolve a freshly-created anchor to (source location, snippet)."""
        if isinstance(anchor, PaperAnchor):
            return None, None

        if isinstance(anchor, SourceRangeAnchor):
            file_path = self.watch_dir / anchor.file
            snippet = capture_snippet(file_path, anchor.line_start, anchor.line_end)
            return (
                ResolvedSource(
                    file=anchor.file,
                    line_start=anchor.line_start,
                    line_end=anchor.line_end,
                    excerpt=snippet,
                ),
                snippet or None,
            )

        if isinstance(anchor, SectionAnchor):
            if self.structure is None:
                self.structure = parse_structure(self.watch_dir)
            match = find_section(self.structure, title=anchor.title, label=anchor.label)
            if match is None:
                return None, None
            file, line_start, line_end = match
            if line_end < 0:
                path = self.watch_dir / file
                try:
                    line_end = len(
                        path.read_text(encoding="utf-8", errors="replace").splitlines()
                    )
                except OSError:
                    line_end = line_start
            # Section anchors don't store a snippet — they re-resolve via
            # the structure parser instead.
            return (
                ResolvedSource(file=file, line_start=line_start, line_end=line_end),
                None,
            )

        if isinstance(anchor, PdfRegionAnchor):
            resolved = _resolve_pdf_region_to_source(self.synctex_data, anchor.page, anchor.bbox)
            if resolved is None:
                return None, None
            file_path = self.watch_dir / resolved.file
            snippet = capture_snippet(file_path, resolved.line_start, resolved.line_end)
            resolved.excerpt = snippet
            return resolved, snippet or None

        return None, None

    async def _handle_reply_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        text = (data.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "text is required"}, status=400)
        try:
            updated = self.comments.reply(
                cid,
                text=text,
                author=data.get("author", "human"),
                edits=data.get("edits") or [],
            )
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(updated)})
        return web.json_response(_comment_to_dict(updated))

    async def _handle_resolve_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        summary = (data.get("summary") or "").strip()
        if not summary:
            return web.json_response({"error": "summary is required"}, status=400)
        try:
            updated = self.comments.resolve(
                cid,
                summary=summary,
                edits=data.get("edits") or [],
                author=data.get("author", "claude"),
            )
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(updated)})
        return web.json_response(_comment_to_dict(updated))

    async def _handle_dismiss_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        reason = (data.get("reason") or "").strip()
        if not reason:
            return web.json_response({"error": "reason is required"}, status=400)
        try:
            updated = self.comments.dismiss(
                cid, reason=reason, author=data.get("author", "human")
            )
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(updated)})
        return web.json_response(_comment_to_dict(updated))

    async def _handle_reopen_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            updated = self.comments.reopen(cid)
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(updated)})
        return web.json_response(_comment_to_dict(updated))

    async def _handle_delete_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        ok = self.comments.delete(cid)
        if not ok:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_deleted", "id": cid})
        return web.json_response({"deleted": cid})

    # ----- SyncTeX -----

    async def _handle_synctex_forward(self, request: web.Request) -> web.Response:
        """source -> PDF: ?file=...&line=N -> {page, x, y, width, height}"""
        if self.synctex_data is None:
            return web.json_response({"error": "no SyncTeX data"}, status=404)
        file = request.query.get("file")
        try:
            line = int(request.query.get("line", "0"))
        except ValueError:
            return web.json_response({"error": "invalid line"}, status=400)
        if not file:
            return web.json_response({"error": "file is required"}, status=400)
        pos = source_to_page(self.synctex_data, file, line)
        if pos is None:
            return web.json_response({"error": "no match"}, status=404)
        return web.json_response(
            {
                "page": pos.page,
                "x": pos.x,
                "y": pos.y,
                "width": pos.width,
                "height": pos.height,
            }
        )

    async def _handle_synctex_reverse(self, request: web.Request) -> web.Response:
        """PDF -> source: ?page=N&y=Y -> {file, line, column}"""
        if self.synctex_data is None:
            return web.json_response({"error": "no SyncTeX data"}, status=404)
        try:
            page = int(request.query.get("page", "0"))
            y_str = request.query.get("y")
            y = float(y_str) if y_str is not None else None
        except ValueError:
            return web.json_response({"error": "invalid page/y"}, status=400)
        src = page_to_source(self.synctex_data, page, y)
        if src is None:
            return web.json_response({"error": "no match"}, status=404)
        return web.json_response({"file": src.file, "line": src.line, "column": src.column})

    async def _handle_goto(self, request: web.Request) -> web.Response:
        """Tell the viewer to scroll/highlight a target.

        Body: {"section": "...", "line": N, "page": N, "file": "..."} (one of section/line/page).
        Broadcasts a goto event over the WebSocket.
        """
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        section = data.get("section")
        line = data.get("line")
        page = data.get("page")
        file = data.get("file")

        target_page: int | None = None

        if section and self.structure is not None:
            match = find_section(self.structure, title=str(section), label=str(section))
            if match and self.synctex_data is not None:
                f, ls, _ = match
                pos = source_to_page(self.synctex_data, f, ls)
                if pos:
                    target_page = pos.page
        elif line and file and self.synctex_data is not None:
            pos = source_to_page(self.synctex_data, str(file), int(line))
            if pos:
                target_page = pos.page
        elif page is not None:
            target_page = int(page)

        if target_page is None:
            return web.json_response({"error": "could not resolve target"}, status=404)

        await self.broadcast({"type": "goto", "page": target_page})
        return web.json_response({"page": target_page})

    # ----- lifecycle -----

    async def start(self, port: int) -> None:
        # Initial compile
        await self.do_compile()
        # Start watcher
        loop = asyncio.get_running_loop()
        self.watcher = TexWatcher(
            watch_dir=self.watch_dir,
            watch_patterns=self.config.watch,
            ignore_patterns=self.config.ignore,
            on_change=self.on_file_change,
        )
        self.watcher.start(loop)
        # Start aiohttp
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        logger.info("texwatch serving on http://127.0.0.1:%d", port)
        # Run until cancelled
        try:
            await asyncio.Event().wait()
        finally:
            if self.watcher:
                self.watcher.stop()
            await runner.cleanup()


def run(config: Config, port: int) -> None:
    """Synchronous entry point: build server, run until KeyboardInterrupt."""
    server = TexWatchServer(config)
    try:
        asyncio.run(server.start(port))
    except KeyboardInterrupt:
        logger.info("Shutting down")

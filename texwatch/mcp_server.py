"""MCP server for texwatch v0.4.0.

Exposes 5 tools to Claude Code via stdio:

    texwatch_paper()                 paper state (sections, labels, citations, comments)
    texwatch_compile()               recompile, return structured errors
    texwatch_comments(status, tags)  list comments (queue of work)
    texwatch_comment(action, ...)    add/reply/resolve/dismiss/reopen/delete
    texwatch_goto(target)            tell the daemon to scroll the viewer (requires daemon)

The MCP server reads/writes the same files the daemon does
(``.texwatch/comments.json`` and the .tex sources).  It does not require
the daemon to be running for paper/compile/comment operations.  The
``goto`` tool is the exception: it speaks HTTP to a running daemon.

Requires: pip install "mcp>=1.0"  (and httpx for goto)
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_deps() -> None:
    if not HAS_MCP:
        print(
            "Error: MCP server requires the 'mcp' package.\n"
            "Install with:\n"
            "  pip install texwatch[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_project():
    """Resolve config + watch_dir + comment store from cwd."""
    from .comments import CommentStore
    from .config import get_watch_dir, load_config

    cfg = load_config()
    watch_dir = get_watch_dir(cfg)
    store = CommentStore(watch_dir / ".texwatch" / "comments.json")
    return cfg, watch_dir, store


def _result_to_dict(result) -> dict[str, Any]:
    if result is None:
        return {"success": None}
    return {
        "success": result.success,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "warnings": [dataclasses.asdict(w) for w in result.warnings],
        "output_file": str(result.output_file) if result.output_file else None,
        "duration_seconds": result.duration_seconds,
        "timestamp": result.timestamp.isoformat() if result.timestamp else None,
    }


def _err(message: str) -> str:
    return json.dumps({"error": message})


def _ok(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _comment_add(
    store,
    cfg,
    watch_dir: Path,
    text: str | None,
    anchor: dict[str, Any] | None,
    author: str,
) -> str:
    """Implementation of ``texwatch_comment(action="add", ...)``.

    Resolves source-bearing anchors via the same helpers the HTTP server
    uses, so the two surfaces produce identical ``resolved_source``
    metadata.  For ``pdf_region`` anchors, loads the cached SyncTeX file
    next to the rendered PDF (no daemon required).
    """
    from .comments import ResolvedSource, anchor_from_dict, capture_snippet
    from .config import get_main_file
    from .server import (
        load_synctex_for_main,
        resolve_pdf_region_to_source,
        resolve_section_to_source,
    )
    from .structure import parse_structure

    if not text or not anchor:
        return _err("add requires text and anchor")

    a = anchor_from_dict(anchor)
    resolved: ResolvedSource | None = None
    snippet: str | None = None
    kind = anchor.get("kind")

    if kind == "source_range":
        file = anchor["file"]
        ls = int(anchor["line_start"])
        le = int(anchor["line_end"])
        snippet = capture_snippet(watch_dir / file, ls, le) or None
        resolved = ResolvedSource(file=file, line_start=ls, line_end=le)
    elif kind == "section":
        resolved = resolve_section_to_source(
            parse_structure(watch_dir), watch_dir, anchor.get("title"), anchor.get("label")
        )
    elif kind == "pdf_region":
        synctex = load_synctex_for_main(get_main_file(cfg))
        bbox = anchor.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        resolved = resolve_pdf_region_to_source(
            synctex,
            int(anchor.get("page", 1)),
            (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        )
        if resolved is not None:
            snippet = (
                capture_snippet(
                    watch_dir / resolved.file, resolved.line_start, resolved.line_end
                )
                or None
            )

    comment = store.add(
        anchor=a,
        text=text,
        author=author,
        resolved_source=resolved,
        snippet=snippet,
    )
    return _ok(comment.to_dict())


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def create_server(daemon_port: int = 8765) -> "FastMCP":
    _check_deps()
    mcp = FastMCP("texwatch")

    @mcp.tool()
    async def texwatch_paper(
        include_comments: bool = True,
        comments_status: str = "open",
    ) -> str:
        """The "what does my paper look like right now" oracle.

        Returns:
          - main_file, watch_dir
          - sections (with title, label, file, line, line_end)
          - compile_cache (pdf path + whether it exists)
          - comments[] (full list of comments at the requested status)

        Section parsing is deliberately the only structure we hand back;
        for labels, citations, and \\input refs, use ``Grep``.

        Args:
            include_comments: if False, the ``comments`` field is omitted
                (useful when you only need orientation, not the queue).
            comments_status: ``"open"`` (default), ``"resolved"``,
                ``"dismissed"``, or ``"all"``.
        """
        from .config import get_main_file
        from .server import structure_to_dict
        from .structure import parse_structure

        cfg, watch_dir, store = _load_project()
        structure = parse_structure(watch_dir)
        pdf_path = get_main_file(cfg).with_suffix(".pdf")

        result: dict[str, Any] = {
            "main_file": cfg.main,
            "watch_dir": str(watch_dir),
            **structure_to_dict(structure, watch_dir),
            "compile_cache": {
                "pdf_exists": pdf_path.exists(),
                "pdf_path": str(pdf_path),
            },
        }
        if include_comments:
            s = None if comments_status == "all" else comments_status
            comments = store.list(status=s)  # type: ignore[arg-type]
            result["comments"] = [c.to_dict() for c in comments]
        return _ok(result)

    @mcp.tool()
    async def texwatch_compile() -> str:
        """Recompile the paper.  Returns structured errors and warnings, each
        with file/line/message and (when possible) source context lines.

        Use after editing source: read the structured errors instead of
        re-reading the full latexmk log.
        """
        from .compiler import compile_tex
        from .config import get_main_file

        cfg, watch_dir, _ = _load_project()
        result = await compile_tex(
            get_main_file(cfg), compiler=cfg.compiler, work_dir=watch_dir
        )
        return _ok(_result_to_dict(result))

    @mcp.tool()
    async def texwatch_comment(
        action: str,
        id: str | None = None,
        text: str | None = None,
        anchor: dict[str, Any] | None = None,
        summary: str | None = None,
        reason: str | None = None,
        edits: list[str] | None = None,
        author: str = "claude",
    ) -> str:
        """Mutate a comment.

        Actions:
          - add:     create new comment.   Required: text + anchor.
          - reply:   append a thread entry.  Required: id + text.
          - resolve: mark resolved.          Required: id + summary.
          - dismiss: mark dismissed.         Required: id + reason.
          - delete:  permanently remove.     Required: id.

        Anchor formats (only for add):
          {"kind": "paper"}
          {"kind": "section", "title": "Methods", "label": "sec:methods"}
          {"kind": "source_range", "file": "intro.tex", "line_start": 42, "line_end": 58}
          {"kind": "pdf_region", "page": 3, "bbox": [x1, y1, x2, y2]}

        Edits is an optional list of strings describing what changed when
        resolving: ["intro.tex:42-58 -> :42-78"].

        To list the comment queue, call ``texwatch_paper()`` (which
        returns comments by default).
        """
        cfg, watch_dir, store = _load_project()
        try:
            if action == "add":
                return _comment_add(store, cfg, watch_dir, text, anchor, author)
            if action == "reply":
                if not id or not text:
                    return _err("reply requires id and text")
                return _ok(
                    store.reply(id, text=text, author=author, edits=edits or []).to_dict()
                )
            if action == "resolve":
                if not id or not summary:
                    return _err("resolve requires id and summary")
                return _ok(
                    store.resolve(
                        id, summary=summary, edits=edits or [], author=author
                    ).to_dict()
                )
            if action == "dismiss":
                if not id or not reason:
                    return _err("dismiss requires id and reason")
                return _ok(store.dismiss(id, reason=reason, author=author).to_dict())
            if action == "delete":
                if not id:
                    return _err("delete requires id")
                return _ok({"deleted": id, "ok": store.delete(id)})
            return _err(f"unknown action: {action}")
        except KeyError as exc:
            return _err(f"comment not found: {exc}")
        except (ValueError, TypeError) as exc:
            return _err(str(exc))

    @mcp.tool()
    async def texwatch_image(
        page: int | None = None,
        bbox: list[float] | None = None,
        source: str | None = None,
        comment_id: str | None = None,
        dpi: int = 150,
    ) -> list[ImageContent | TextContent]:
        """Render a PDF region as PNG and return it for visual analysis.

        Use exactly one of:
          page=N                         full page
          page=N + bbox=[x1,y1,x2,y2]    region in PDF points
          source="file.tex:lstart-lend"  SyncTeX-resolved region
          comment_id="c-..."             the region a comment is anchored to

        Returns ImageContent (base64 PNG) on success.  Use this when text
        alone won't tell you what's wrong: figure layout, equation
        rendering, overfull boxes, table positioning, or to verify a fix
        looks right.

        Combine with texwatch_paper() for the workflow:
          1. texwatch_paper()                          # see open comments
          2. for each: texwatch_image(comment_id=...)  # see the rendered
                                                       # region the human
                                                       # anchored
          3. Read source, Edit, then texwatch_compile + texwatch_image
             again to verify visually.
        """
        from .comments import PdfRegionAnchor, PaperAnchor
        from .config import get_main_file
        from .server import _parse_source_range
        from . import imaging

        cfg, _, store = _load_project()
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        if not pdf_path.exists():
            return [TextContent(type="text",
                text=_err("no PDF on disk; run texwatch_compile() first"))]

        try:
            resolved_page, resolved_bbox = _resolve_image_target(
                store, cfg, page, bbox, source, comment_id
            )
        except ValueError as exc:
            return [TextContent(type="text", text=_err(str(exc)))]

        try:
            if resolved_bbox is None:
                png = imaging.render_page(pdf_path, resolved_page, dpi=dpi)
            else:
                png = imaging.render_region(pdf_path, resolved_page, resolved_bbox, dpi=dpi)
        except imaging.ImagingError as exc:
            return [TextContent(type="text", text=_err(str(exc)))]

        import base64
        return [ImageContent(
            type="image",
            data=base64.b64encode(png).decode("ascii"),
            mimeType="image/png",
        )]

    @mcp.tool()
    async def texwatch_goto(target: str, port: int = daemon_port) -> str:
        """Tell a running daemon to scroll the viewer to a target.

        target: a section title, "pN" (page), file:line, or just N (line in main file).
        Requires the texwatch server to be running.
        """
        try:
            import httpx
        except ImportError:
            return _err("httpx not installed; install texwatch[mcp]")

        cfg, _, _ = _load_project()
        body = parse_goto_target(target, default_file=cfg.main)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"http://127.0.0.1:{port}/goto", json=body)
                return resp.text
        except Exception as exc:
            return _err(f"daemon at port {port} not reachable: {exc}")

    return mcp


import re

# A LaTeX-style label: short alpha prefix + colon + identifier without spaces.
# Matches ``sec:methods``, ``eq:foo-bar``, ``thm:main``.  Does *not* match
# ``Introduction: A Survey`` (space) or filenames (long prefix / has dot).
_LABEL_LIKE = re.compile(r"^[a-zA-Z]{2,8}:[A-Za-z0-9_.\-:]+$")


def _resolve_image_target(
    store,
    cfg,
    page: int | None,
    bbox: list[float] | None,
    source: str | None,
    comment_id: str | None,
) -> tuple[int, tuple[float, float, float, float] | None]:
    """Standalone version of TexWatchServer._resolve_image_target for the
    MCP path (which talks to the file system, not a running daemon).

    Raises ValueError with a user-facing message on bad inputs.
    """
    from .config import get_main_file
    from .server import _parse_source_range, load_synctex_for_main
    from . import imaging

    primary = sum(1 for v in (page, source, comment_id) if v not in (None, ""))
    if primary != 1:
        raise ValueError("specify exactly one of page, source, or comment_id")

    if comment_id:
        c = store.get(comment_id)
        if c is None:
            raise ValueError(f"no comment {comment_id}")
        if c.anchor.kind == "pdf_region":
            return c.anchor.page, tuple(c.anchor.bbox)  # type: ignore[attr-defined,return-value]
        if c.anchor.kind == "paper":
            raise ValueError("paper anchors have no PDF region")
        if c.resolved_source is None:
            raise ValueError("comment is not anchored to a renderable region")
        synctex = load_synctex_for_main(get_main_file(cfg))
        if synctex is None:
            raise ValueError("no SyncTeX data; cannot resolve comment region")
        pair = imaging.resolve_source_to_region(
            synctex,
            c.resolved_source.file,
            c.resolved_source.line_start,
            c.resolved_source.line_end,
        )
        if pair is None:
            raise ValueError("comment's source range has no PDF coverage")
        return pair

    if source:
        file, ls, le = _parse_source_range(source)
        synctex = load_synctex_for_main(get_main_file(cfg))
        if synctex is None:
            raise ValueError("no SyncTeX data")
        pair = imaging.resolve_source_to_region(synctex, file, ls, le)
        if pair is None:
            raise ValueError("no PDF region for this source range")
        return pair

    # page (with optional bbox)
    if page is None:
        raise ValueError("page must be an integer")
    resolved_bbox: tuple[float, float, float, float] | None = None
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError("bbox must have exactly 4 values")
        resolved_bbox = (
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )
    return int(page), resolved_bbox


def parse_goto_target(target: str, default_file: str) -> dict[str, Any]:
    """Convert a CLI/MCP goto target string into a request body for ``/goto``.

    Recognized forms (in order):
      ``pN``         -> ``{"page": N}``
      ``N``          -> ``{"line": N, "file": default_file}``
      ``FILE:N``     -> ``{"file": FILE, "line": N}``  (right-hand side digits)
      ``sec:foo``    -> ``{"label": "sec:foo"}``       (LaTeX label syntax)
      anything else  -> ``{"section": target}``
    """
    if target.startswith("p") and target[1:].isdigit():
        return {"page": int(target[1:])}
    if target.isdigit():
        return {"line": int(target), "file": default_file}
    if ":" in target and target.rsplit(":", 1)[1].isdigit():
        file, line = target.rsplit(":", 1)
        return {"file": file, "line": int(line)}
    if _LABEL_LIKE.match(target):
        return {"label": target}
    return {"section": target}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def main(port: int = 8765) -> None:
    """Run the MCP server with stdio transport."""
    _check_deps()
    mcp = create_server(daemon_port=port)
    asyncio.run(mcp.run_stdio_async())

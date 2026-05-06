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
    tags: list[str] | None,
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
        resolved = ResolvedSource(
            file=file, line_start=ls, line_end=le, excerpt=snippet or ""
        )
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
            resolved.excerpt = snippet or ""

    comment = store.add(
        anchor=a,
        text=text,
        author=author,
        tags=tags or [],
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
    async def texwatch_paper() -> str:
        """Full paper state: sections (with line ranges), labels, citations,
        \\input refs, last compile result if available, and comment summary.

        This is the "what does my paper look like right now" oracle.  Call
        once per session to orient.
        """
        from .config import get_main_file
        from .server import structure_to_dict
        from .structure import parse_structure

        cfg, watch_dir, store = _load_project()
        structure = parse_structure(watch_dir)

        # Read cached compile output (use texwatch_compile() to trigger a build).
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        open_comments = store.list(status="open")

        return _ok(
            {
                "main_file": cfg.main,
                "watch_dir": str(watch_dir),
                **structure_to_dict(structure, watch_dir),
                "compile_cache": {
                    "pdf_exists": pdf_path.exists(),
                    "pdf_path": str(pdf_path),
                },
                "comments": {
                    "open": len(open_comments),
                    "resolved": len(store.list(status="resolved")),
                    "dismissed": len(store.list(status="dismissed")),
                    "stale": sum(1 for c in open_comments if c.stale),
                },
            }
        )

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
    async def texwatch_comments(
        status: str = "open",
        tags: list[str] | None = None,
    ) -> str:
        """List paper review comments.

        status: "open" | "resolved" | "dismissed" | "all"
        tags: optional filter; comments with any matching tag are returned.

        Each comment has an anchor (paper / section / source_range / pdf_region),
        a thread of entries, an optional resolved_source, and a stale flag.
        """
        _, _, store = _load_project()
        comments = store.list(status=None if status == "all" else status, tags=tags)
        return _ok({"comments": [c.to_dict() for c in comments]})

    @mcp.tool()
    async def texwatch_comment(
        action: str,
        id: str | None = None,
        text: str | None = None,
        anchor: dict[str, Any] | None = None,
        summary: str | None = None,
        reason: str | None = None,
        edits: list[str] | None = None,
        tags: list[str] | None = None,
        author: str = "claude",
    ) -> str:
        """Mutate a comment.

        Actions:
          - add:     create new comment.   Required: text + anchor.
          - reply:   append a thread entry.  Required: id + text.
          - resolve: mark resolved.          Required: id + summary.
          - dismiss: mark dismissed.         Required: id + reason.
          - reopen:  reopen a closed one.    Required: id.
          - delete:  permanently remove.     Required: id.

        Anchor formats (only for add):
          {"kind": "paper"}
          {"kind": "section", "title": "Methods", "label": "sec:methods"}
          {"kind": "source_range", "file": "intro.tex", "line_start": 42, "line_end": 58}
          {"kind": "pdf_region", "page": 3, "bbox": [x1, y1, x2, y2]}

        Edits is a list of strings describing what was changed when resolving:
          ["intro.tex:42-58 -> :42-78", "added theorem 2.1"]
        """
        cfg, watch_dir, store = _load_project()

        # Each handler returns the JSON response or an error string.
        # We share the KeyError -> "not found" trap across all branches.
        try:
            if action == "add":
                return _comment_add(store, cfg, watch_dir, text, anchor, author, tags)
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
            if action == "reopen":
                if not id:
                    return _err("reopen requires id")
                return _ok(store.reopen(id, author=author).to_dict())
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

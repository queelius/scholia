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
    from mcp.types import TextContent

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


def _structure_to_paper_dict(structure, watch_dir: Path) -> dict[str, Any]:
    from .structure import find_section

    sections_with_ends: list[dict] = []
    for s in structure.sections:
        match = find_section(structure, title=s.title, label=s.label)
        line_end = match[2] if match else -1
        if line_end < 0:
            try:
                line_end = len(
                    (watch_dir / s.file).read_text(errors="replace").splitlines()
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

    return {
        "sections": sections_with_ends,
        "labels": [{"name": l.name, "file": l.file, "line": l.line} for l in structure.labels],
        "citations": [{"key": c.key, "file": c.file, "line": c.line} for c in structure.citations],
        "inputs": [{"path": i.path, "file": i.file, "line": i.line} for i in structure.inputs],
    }


def _build_anchor(anchor_dict: dict[str, Any]):
    from .comments import anchor_from_dict

    return anchor_from_dict(anchor_dict)


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
        from .compiler import compile_tex
        from .config import get_main_file
        from .structure import parse_structure

        cfg, watch_dir, store = _load_project()
        structure = parse_structure(watch_dir)

        # Try to find an existing compile output without re-running latexmk
        # (we only return what's cached on disk; use texwatch_compile() to
        # actively trigger a build).
        main = get_main_file(cfg)
        pdf_path = main.with_suffix(".pdf")
        cached = {"pdf_exists": pdf_path.exists(), "pdf_path": str(pdf_path)}

        comment_summary = {
            "open": len(store.list(status="open")),
            "resolved": len(store.list(status="resolved")),
            "dismissed": len(store.list(status="dismissed")),
            "stale": sum(1 for c in store.list(status="open") if c.stale),
        }

        return json.dumps(
            {
                "main_file": cfg.main,
                "watch_dir": str(watch_dir),
                **_structure_to_paper_dict(structure, watch_dir),
                "compile_cache": cached,
                "comments": comment_summary,
            },
            indent=2,
        )

    @mcp.tool()
    async def texwatch_compile() -> str:
        """Recompile the paper.  Returns structured errors and warnings, each
        with file/line/message and (when possible) source context lines.

        Use after editing source: read the structured errors instead of
        re-reading the full latexmk log.
        """
        from .compiler import compile_tex
        from .config import get_main_file, get_watch_dir

        cfg, watch_dir, _ = _load_project()
        main = get_main_file(cfg)
        result = await compile_tex(main, compiler=cfg.compiler, work_dir=watch_dir)
        return json.dumps(_result_to_dict(result), indent=2)

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
        cfg, watch_dir, store = _load_project()
        s = None if status == "all" else status
        comments = store.list(status=s, tags=tags)
        return json.dumps(
            {"comments": [c.to_dict() for c in comments]},
            indent=2,
            ensure_ascii=False,
        )

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
        from .comments import ResolvedSource, capture_snippet
        from .structure import find_section, parse_structure

        cfg, watch_dir, store = _load_project()

        try:
            if action == "add":
                if not text or not anchor:
                    return json.dumps({"error": "add requires text and anchor"})
                a = _build_anchor(anchor)
                # Resolve and capture snippet for source/section anchors
                resolved: ResolvedSource | None = None
                snippet: str | None = None
                if anchor["kind"] == "source_range":
                    file = anchor["file"]
                    ls = int(anchor["line_start"])
                    le = int(anchor["line_end"])
                    snippet = capture_snippet(watch_dir / file, ls, le) or None
                    resolved = ResolvedSource(file=file, line_start=ls, line_end=le, excerpt=snippet or "")
                elif anchor["kind"] == "section":
                    structure = parse_structure(watch_dir)
                    match = find_section(
                        structure, title=anchor.get("title"), label=anchor.get("label")
                    )
                    if match is not None:
                        f, ls, le = match
                        if le < 0:
                            try:
                                le = len(
                                    (watch_dir / f).read_text(errors="replace").splitlines()
                                )
                            except OSError:
                                le = ls
                        resolved = ResolvedSource(file=f, line_start=ls, line_end=le)

                comment = store.add(
                    anchor=a,
                    text=text,
                    author=author,
                    tags=tags or [],
                    resolved_source=resolved,
                    snippet=snippet,
                )
                return json.dumps(comment.to_dict(), indent=2)

            if action == "reply":
                if not id or not text:
                    return json.dumps({"error": "reply requires id and text"})
                c = store.reply(id, text=text, author=author, edits=edits or [])
                return json.dumps(c.to_dict(), indent=2)

            if action == "resolve":
                if not id or not summary:
                    return json.dumps({"error": "resolve requires id and summary"})
                c = store.resolve(id, summary=summary, edits=edits or [], author=author)
                return json.dumps(c.to_dict(), indent=2)

            if action == "dismiss":
                if not id or not reason:
                    return json.dumps({"error": "dismiss requires id and reason"})
                c = store.dismiss(id, reason=reason, author=author)
                return json.dumps(c.to_dict(), indent=2)

            if action == "reopen":
                if not id:
                    return json.dumps({"error": "reopen requires id"})
                c = store.reopen(id, author=author)
                return json.dumps(c.to_dict(), indent=2)

            if action == "delete":
                if not id:
                    return json.dumps({"error": "delete requires id"})
                ok = store.delete(id)
                return json.dumps({"deleted": id, "ok": ok})

            return json.dumps({"error": f"unknown action: {action}"})

        except KeyError as exc:
            return json.dumps({"error": f"comment not found: {exc}"})
        except (ValueError, KeyError, TypeError) as exc:
            return json.dumps({"error": str(exc)})

    @mcp.tool()
    async def texwatch_goto(target: str, port: int = daemon_port) -> str:
        """Tell a running daemon to scroll the viewer to a target.

        target: a section title, "pN" (page), file:line, or just N (line in main file).
        Requires the texwatch server to be running.
        """
        try:
            import httpx
        except ImportError:
            return json.dumps({"error": "httpx not installed; install texwatch[mcp]"})

        body: dict[str, Any] = {}
        if target.startswith("p") and target[1:].isdigit():
            body["page"] = int(target[1:])
        elif target.isdigit():
            cfg, _, _ = _load_project()
            body["line"] = int(target)
            body["file"] = cfg.main
        elif ":" in target and target.rsplit(":", 1)[1].isdigit():
            file, line = target.rsplit(":", 1)
            body["file"] = file
            body["line"] = int(line)
        else:
            body["section"] = target

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"http://127.0.0.1:{port}/goto", json=body)
                return resp.text
        except Exception as exc:
            return json.dumps({"error": f"daemon at port {port} not reachable: {exc}"})

    return mcp


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def main(port: int = 8765) -> None:
    """Run the MCP server with stdio transport."""
    _check_deps()
    mcp = create_server(daemon_port=port)
    asyncio.run(mcp.run_stdio_async())

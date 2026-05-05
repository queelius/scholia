"""texwatch command-line interface (v0.4.0).

Subcommands:
    serve      run the daemon (default if no subcommand)
    init       scaffold .texwatch.yaml
    compile    one-shot compile, print structured errors
    comment    add/list/show/resolve/dismiss/reopen/delete
    goto       tell a running daemon to scroll the viewer
    mcp        run the MCP server (stdio transport)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .comments import (
    CommentStore,
    PaperAnchor,
    PdfRegionAnchor,
    SectionAnchor,
    SourceRangeAnchor,
    capture_snippet,
)
from .config import Config, create_config, find_config, load_config
from .structure import find_section, parse_structure


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run as run_server

    cfg = load_config(main_file=getattr(args, "main", None))
    port = args.port or cfg.port
    print(f"texwatch v0.4.0  serving {cfg.main} at http://127.0.0.1:{port}", file=sys.stderr)
    run_server(cfg, port=port)
    return 0


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    existing = find_config()
    if existing and not args.force:
        print(f"Config already exists: {existing}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        return 1
    path = create_config(main=args.main, port=args.port or 8765)
    print(f"Wrote {path}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Compile (one-shot)
# ---------------------------------------------------------------------------


def cmd_compile(args: argparse.Namespace) -> int:
    from .compiler import compile_tex
    from .config import get_main_file, get_watch_dir

    cfg = load_config(main_file=args.main)
    main = get_main_file(cfg)
    watch_dir = get_watch_dir(cfg)

    result = asyncio.run(compile_tex(main, compiler=cfg.compiler, work_dir=watch_dir))

    if args.json:
        print(json.dumps(_compile_result_dict(result), indent=2))
    else:
        if result.success:
            print(f"compile succeeded in {result.duration_seconds:.2f}s")
        else:
            print(f"compile FAILED ({len(result.errors)} errors)", file=sys.stderr)
        for err in result.errors:
            loc = f"{err.file}:{err.line}" if err.line else err.file
            print(f"  ERROR  {loc}  {err.message}", file=sys.stderr)
            if err.context:
                for line in err.context:
                    print(f"           {line}", file=sys.stderr)
        for w in result.warnings:
            loc = f"{w.file}:{w.line}" if w.line else w.file
            print(f"  warn   {loc}  {w.message}", file=sys.stderr)
    return 0 if result.success else 1


def _compile_result_dict(result) -> dict:
    import dataclasses

    return {
        "success": result.success,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "warnings": [dataclasses.asdict(w) for w in result.warnings],
        "output_file": str(result.output_file) if result.output_file else None,
        "duration_seconds": result.duration_seconds,
    }


# ---------------------------------------------------------------------------
# Goto (requires running daemon)
# ---------------------------------------------------------------------------


def cmd_goto(args: argparse.Namespace) -> int:
    cfg = load_config()
    port = args.port or cfg.port
    target = args.target

    body: dict = {}
    # Heuristics: page numbers (pN), file:line, or section title
    if target.startswith("p") and target[1:].isdigit():
        body["page"] = int(target[1:])
    elif target.isdigit():
        body["line"] = int(target)
        body["file"] = cfg.main
    elif ":" in target and target.rsplit(":", 1)[1].isdigit():
        file, line = target.rsplit(":", 1)
        body["file"] = file
        body["line"] = int(line)
    else:
        body["section"] = target

    try:
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/goto",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(resp.read().decode())
        return 0
    except Exception as exc:
        print(f"could not reach daemon at {port}: {exc}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as mcp_main

    cfg = load_config()
    port = args.port or cfg.port
    mcp_main(port=port)
    return 0


# ---------------------------------------------------------------------------
# Comment subcommands
# ---------------------------------------------------------------------------


def _store_for_cwd() -> CommentStore:
    cfg = load_config()
    if cfg.config_path:
        watch_dir = cfg.config_path.parent
    else:
        watch_dir = Path.cwd()
    return CommentStore(watch_dir / ".texwatch" / "comments.json")


def _watch_dir() -> Path:
    cfg = load_config()
    return cfg.config_path.parent if cfg.config_path else Path.cwd()


def cmd_comment_add(args: argparse.Namespace) -> int:
    """Create a comment.

    Anchor flags (mutually exclusive, at least one required):
        --paper                           global paper-level note
        --section <title>                 section by title (label optional via --label)
        --source <file>:<lstart>-<lend>   explicit source range
        --pdf <page>:<x1>,<y1>,<x2>,<y2>  PDF region (advanced)
    """
    text = args.text
    tags = args.tag or []

    anchor = None
    resolved = None
    snippet = None
    watch_dir = _watch_dir()

    if args.paper:
        anchor = PaperAnchor()
    elif args.section is not None:
        anchor = SectionAnchor(title=args.section, label=args.label)
        # Try to resolve immediately for convenience
        structure = parse_structure(watch_dir)
        match = find_section(structure, title=args.section, label=args.label)
        if match is None:
            print(f"warning: no section matching {args.section!r}", file=sys.stderr)
        else:
            from .comments import ResolvedSource

            file, ls, le = match
            if le < 0:
                try:
                    le = len((watch_dir / file).read_text(errors="replace").splitlines())
                except OSError:
                    le = ls
            resolved = ResolvedSource(file=file, line_start=ls, line_end=le)
    elif args.source is not None:
        try:
            file, lines = args.source.rsplit(":", 1)
            ls_str, le_str = (lines.split("-") + [None])[:2]
            ls = int(ls_str)
            le = int(le_str) if le_str else ls
        except (ValueError, IndexError):
            print("--source must be FILE:LSTART-LEND (e.g. intro.tex:42-58)", file=sys.stderr)
            return 1
        anchor = SourceRangeAnchor(file=file, line_start=ls, line_end=le)
        snippet = capture_snippet(watch_dir / file, ls, le) or None
        from .comments import ResolvedSource

        resolved = ResolvedSource(file=file, line_start=ls, line_end=le, excerpt=snippet or "")
    elif args.pdf is not None:
        try:
            page_str, bbox_str = args.pdf.split(":", 1)
            page = int(page_str)
            x1, y1, x2, y2 = (float(v) for v in bbox_str.split(","))
        except (ValueError, IndexError):
            print(
                "--pdf must be PAGE:X1,Y1,X2,Y2 (PDF points)", file=sys.stderr
            )
            return 1
        anchor = PdfRegionAnchor(page=page, bbox=(x1, y1, x2, y2))
    else:
        print("one of --paper/--section/--source/--pdf is required", file=sys.stderr)
        return 1

    store = _store_for_cwd()
    comment = store.add(
        anchor=anchor,
        text=text,
        author=args.author,
        tags=tags,
        resolved_source=resolved,
        snippet=snippet,
    )
    if args.json:
        print(json.dumps(comment.to_dict(), indent=2))
    else:
        print(f"{comment.id}  {anchor_summary(anchor)}  {text}")
    return 0


def cmd_comment_list(args: argparse.Namespace) -> int:
    store = _store_for_cwd()
    status = args.status if args.status != "all" else None
    comments = store.list(status=status, tags=args.tag or None)

    if args.json:
        print(json.dumps([c.to_dict() for c in comments], indent=2))
        return 0

    if not comments:
        print("(no comments)")
        return 0

    for c in comments:
        flag = " [STALE]" if c.stale else ""
        tag_str = " " + " ".join(f"#{t}" for t in c.tags) if c.tags else ""
        print(f"{c.id}  [{c.status}]{flag}  {anchor_summary(c.anchor)}{tag_str}")
        print(f"          {c.text}")
        if len(c.thread) > 1:
            print(f"          ({len(c.thread)} entries in thread)")
    return 0


def cmd_comment_show(args: argparse.Namespace) -> int:
    store = _store_for_cwd()
    c = store.get(args.id)
    if c is None:
        print(f"no comment {args.id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(c.to_dict(), indent=2))
        return 0
    print(f"{c.id}  [{c.status}]  {anchor_summary(c.anchor)}")
    if c.tags:
        print(f"  tags: {', '.join(c.tags)}")
    if c.resolved_source:
        rs = c.resolved_source
        print(f"  source: {rs.file}:{rs.line_start}-{rs.line_end}")
    if c.stale:
        print("  STALE: source has shifted; re-anchor or dismiss")
    print(f"  created: {c.created}")
    print()
    for entry in c.thread:
        print(f"  [{entry.author} {entry.at}]")
        for line in entry.text.splitlines() or [""]:
            print(f"    {line}")
        for edit in entry.edits:
            print(f"    edit: {edit}")
    return 0


def cmd_comment_resolve(args: argparse.Namespace) -> int:
    store = _store_for_cwd()
    try:
        c = store.resolve(
            args.id,
            summary=args.summary,
            edits=args.edit or [],
            author=args.author,
        )
    except KeyError:
        print(f"no comment {args.id}", file=sys.stderr)
        return 1
    print(f"resolved {c.id}")
    return 0


def cmd_comment_dismiss(args: argparse.Namespace) -> int:
    store = _store_for_cwd()
    try:
        c = store.dismiss(args.id, reason=args.reason, author=args.author)
    except KeyError:
        print(f"no comment {args.id}", file=sys.stderr)
        return 1
    print(f"dismissed {c.id}")
    return 0


def cmd_comment_reopen(args: argparse.Namespace) -> int:
    store = _store_for_cwd()
    try:
        c = store.reopen(args.id, author=args.author)
    except KeyError:
        print(f"no comment {args.id}", file=sys.stderr)
        return 1
    print(f"reopened {c.id}")
    return 0


def cmd_comment_delete(args: argparse.Namespace) -> int:
    store = _store_for_cwd()
    if store.delete(args.id):
        print(f"deleted {args.id}")
        return 0
    print(f"no comment {args.id}", file=sys.stderr)
    return 1


def anchor_summary(anchor) -> str:
    if isinstance(anchor, PaperAnchor):
        return "[paper]"
    if isinstance(anchor, SectionAnchor):
        return f"[section: {anchor.title}]"
    if isinstance(anchor, SourceRangeAnchor):
        return f"[{anchor.file}:{anchor.line_start}-{anchor.line_end}]"
    if isinstance(anchor, PdfRegionAnchor):
        return f"[pdf p{anchor.page}]"
    return "[?]"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="texwatch",
        description=(
            "Live-rebuild + review-style commenting for LaTeX papers. "
            "Run with no arguments to start the server."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    parser.add_argument("--port", type=int, help="HTTP port (default: from config or 8765)")

    sub = parser.add_subparsers(dest="cmd")

    # serve
    p = sub.add_parser("serve", help="run the daemon (default)")
    p.add_argument("--main", help="main .tex file (overrides config)")
    p.set_defaults(func=cmd_serve)

    # init
    p = sub.add_parser("init", help="scaffold .texwatch.yaml")
    p.add_argument("--main", default="paper.tex")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    # compile
    p = sub.add_parser("compile", help="one-shot compile")
    p.add_argument("--main", help="main .tex file (overrides config)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_compile)

    # goto
    p = sub.add_parser("goto", help="tell the daemon to scroll the viewer")
    p.add_argument("target", help="section title, line number, file:line, or pN")
    p.set_defaults(func=cmd_goto)

    # mcp
    p = sub.add_parser("mcp", help="run the MCP server (stdio)")
    p.set_defaults(func=cmd_mcp)

    # comment
    cp = sub.add_parser("comment", help="manage paper review comments")
    csub = cp.add_subparsers(dest="comment_cmd", required=True)

    add = csub.add_parser("add", help="create a comment")
    add.add_argument("text", help="comment body")
    anchor_grp = add.add_mutually_exclusive_group()
    anchor_grp.add_argument("--paper", action="store_true", help="paper-level (global)")
    anchor_grp.add_argument("--section", help="section title")
    anchor_grp.add_argument("--source", help="FILE:LSTART-LEND")
    anchor_grp.add_argument("--pdf", help="PAGE:X1,Y1,X2,Y2 (PDF points)")
    add.add_argument("--label", help="section label (used with --section)")
    add.add_argument("--tag", action="append", help="tag (repeatable)")
    add.add_argument("--author", default="human", choices=["human", "claude"])
    add.add_argument("--json", action="store_true")
    add.set_defaults(func=cmd_comment_add)

    lst = csub.add_parser("list", help="list comments")
    lst.add_argument(
        "--status",
        default="open",
        choices=["open", "resolved", "dismissed", "all"],
    )
    lst.add_argument("--tag", action="append")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=cmd_comment_list)

    sh = csub.add_parser("show", help="show a single comment in full")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_comment_show)

    res = csub.add_parser("resolve", help="mark a comment resolved")
    res.add_argument("id")
    res.add_argument("summary", help="what was done")
    res.add_argument("--edit", action="append", help="edit description (repeatable)")
    res.add_argument("--author", default="claude", choices=["human", "claude"])
    res.set_defaults(func=cmd_comment_resolve)

    dis = csub.add_parser("dismiss", help="dismiss a comment")
    dis.add_argument("id")
    dis.add_argument("reason")
    dis.add_argument("--author", default="human", choices=["human", "claude"])
    dis.set_defaults(func=cmd_comment_dismiss)

    rop = csub.add_parser("reopen", help="reopen a closed comment")
    rop.add_argument("id")
    rop.add_argument("--author", default="human", choices=["human", "claude"])
    rop.set_defaults(func=cmd_comment_reopen)

    dlt = csub.add_parser("delete", help="permanently delete a comment")
    dlt.add_argument("id")
    dlt.set_defaults(func=cmd_comment_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))

    if not getattr(args, "func", None):
        # Default: serve
        args.main = None
        return cmd_serve(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

"""Comment thread storage for paper review.

texwatch v0.4.0 reframes the tool as a code-review-style commenting system
for LaTeX papers: the human is the reviewer, Claude Code is the author.
This module provides the data model, JSON-backed storage, and anchor-
durability logic.

Anchor types
------------
- ``pdf_region``: a bounding box on a PDF page; resolved to source lines via SyncTeX
- ``section``: a logical section by title and/or label
- ``source_range``: an explicit file + line range
- ``paper``: a global comment, no anchor

Threads
-------
Each comment carries an ordered list of :class:`ThreadEntry` so the human
and Claude Code can converse about a region (request → action → follow-up).

Staleness
---------
When source code shifts beneath an anchor (line numbers move, sections
renamed, content rewritten), comments must either reattach themselves to
the new location or report that they have lost the thread.  We use:

1. A ``snippet`` (a few lines of source content captured at comment creation),
   used as a content-addressed anchor that survives line shifts.
2. Structural anchors (section title/label) when applicable.
3. A boolean ``stale`` flag set when neither resolution succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anchor types
# ---------------------------------------------------------------------------


AnchorKind = Literal["pdf_region", "section", "source_range", "paper"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Short, URL-safe comment id (~6 hex chars, prefixed)."""
    return "c-" + secrets.token_hex(4)


@dataclass
class PdfRegionAnchor:
    page: int
    bbox: tuple[float, float, float, float]  # (x1, y1, x2, y2) PDF points
    kind: Literal["pdf_region"] = "pdf_region"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "page": self.page, "bbox": list(self.bbox)}


@dataclass
class SectionAnchor:
    title: str
    label: str | None = None
    kind: Literal["section"] = "section"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "title": self.title}
        if self.label is not None:
            d["label"] = self.label
        return d


@dataclass
class SourceRangeAnchor:
    file: str
    line_start: int
    line_end: int
    kind: Literal["source_range"] = "source_range"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass
class PaperAnchor:
    kind: Literal["paper"] = "paper"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind}


Anchor = PdfRegionAnchor | SectionAnchor | SourceRangeAnchor | PaperAnchor


def anchor_from_dict(d: dict[str, Any]) -> Anchor:
    """Reconstruct an Anchor from its dict form."""
    kind = d.get("kind")
    if kind == "pdf_region":
        bbox = d.get("bbox") or [0, 0, 0, 0]
        return PdfRegionAnchor(
            page=int(d.get("page", 1)),
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        )
    if kind == "section":
        return SectionAnchor(title=str(d.get("title", "")), label=d.get("label"))
    if kind == "source_range":
        return SourceRangeAnchor(
            file=str(d.get("file", "")),
            line_start=int(d.get("line_start", 0)),
            line_end=int(d.get("line_end", 0)),
        )
    if kind == "paper":
        return PaperAnchor()
    raise ValueError(f"Unknown anchor kind: {kind!r}")


# ---------------------------------------------------------------------------
# Resolved source location
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSource:
    """The source location an anchor currently points at.

    For PDF region / section anchors, this is computed by the server from
    SyncTeX or document structure.  Stored alongside the comment so Claude
    Code can read the comment without re-resolving.
    """

    file: str
    line_start: int
    line_end: int
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResolvedSource":
        return cls(
            file=str(d.get("file", "")),
            line_start=int(d.get("line_start", 0)),
            line_end=int(d.get("line_end", 0)),
            excerpt=str(d.get("excerpt", "")),
        )


# ---------------------------------------------------------------------------
# Thread entries and comments
# ---------------------------------------------------------------------------


Author = Literal["human", "claude"]
Status = Literal["open", "resolved", "dismissed"]


@dataclass
class ThreadEntry:
    author: Author
    at: str
    text: str
    edits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"author": self.author, "at": self.at, "text": self.text}
        if self.edits:
            d["edits"] = list(self.edits)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ThreadEntry":
        return cls(
            author=d.get("author", "human"),  # type: ignore[arg-type]
            at=str(d.get("at", _now())),
            text=str(d.get("text", "")),
            edits=list(d.get("edits", []) or []),
        )


@dataclass
class Comment:
    id: str
    anchor: Anchor
    thread: list[ThreadEntry] = field(default_factory=list)
    status: Status = "open"
    tags: list[str] = field(default_factory=list)
    resolved_source: ResolvedSource | None = None
    snippet: str | None = None
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    stale: bool = False

    @property
    def text(self) -> str:
        """The original comment text (first thread entry)."""
        return self.thread[0].text if self.thread else ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "anchor": self.anchor.to_dict(),
            "thread": [e.to_dict() for e in self.thread],
            "status": self.status,
            "created": self.created,
            "updated": self.updated,
        }
        if self.tags:
            d["tags"] = list(self.tags)
        if self.resolved_source is not None:
            d["resolved_source"] = self.resolved_source.to_dict()
        if self.snippet is not None:
            d["snippet"] = self.snippet
        if self.stale:
            d["stale"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Comment":
        rs = d.get("resolved_source")
        return cls(
            id=str(d["id"]),
            anchor=anchor_from_dict(d["anchor"]),
            thread=[ThreadEntry.from_dict(e) for e in d.get("thread", [])],
            status=d.get("status", "open"),  # type: ignore[arg-type]
            tags=list(d.get("tags", []) or []),
            resolved_source=ResolvedSource.from_dict(rs) if rs else None,
            snippet=d.get("snippet"),
            created=str(d.get("created", _now())),
            updated=str(d.get("updated", _now())),
            stale=bool(d.get("stale", False)),
        )


# ---------------------------------------------------------------------------
# Snippet capture and fuzzy match
# ---------------------------------------------------------------------------


def capture_snippet(
    file: Path, line_start: int, line_end: int, context: int = 2
) -> str:
    """Read source lines [line_start..line_end] +/- *context*.

    Returns a string used as a content-addressed anchor for staleness
    checks.  Empty string if the file can't be read.
    """
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, line_start - 1 - context)
    end = min(len(lines), line_end + context)
    return "\n".join(lines[start:end])


def _strip_for_match(s: str) -> str:
    """Normalize whitespace for fuzzy snippet matching."""
    return re.sub(r"\s+", " ", s).strip()


def find_snippet(snippet: str, file: Path) -> tuple[int, int] | None:
    """Locate *snippet* in *file*, return ``(start_line, end_line)`` 1-indexed.

    Tries exact match first, then whitespace-normalized match.  Returns
    None if the snippet content can no longer be located.
    """
    if not snippet.strip():
        return None
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Exact match
    idx = text.find(snippet)
    if idx >= 0:
        before = text[:idx]
        start_line = before.count("\n") + 1
        end_line = start_line + snippet.count("\n")
        return (start_line, end_line)

    # Whitespace-normalized fallback: line-by-line sliding window
    src_lines = text.splitlines()
    snip_lines = snippet.splitlines()
    if not snip_lines:
        return None
    target = [_strip_for_match(line) for line in snip_lines]
    target_joined = " ".join(t for t in target if t)
    if not target_joined:
        return None

    n = len(snip_lines)
    for i in range(0, len(src_lines) - n + 1):
        window = [_strip_for_match(line) for line in src_lines[i : i + n]]
        joined = " ".join(w for w in window if w)
        if joined == target_joined:
            return (i + 1, i + n)
    return None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


STORE_VERSION = 1


class CommentStore:
    """JSON-backed comment storage.

    Comments live in ``<watch_dir>/.texwatch/comments.json``.  The file
    is small (one paper, typically <1k comments), so we read/write the
    whole file on each operation; concurrent writers are not supported.

    Atomic writes use a temp file + rename to avoid partial writes.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"version": STORE_VERSION, "comments": []})

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("comments: failed to read %s (%s); starting empty", self.path, exc)
            return {"version": STORE_VERSION, "comments": []}

    def _write(self, data: dict[str, Any]) -> None:
        # Atomic write: temp file + rename
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".comments-",
            suffix=".tmp",
            delete=False,
        ) as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            tmp = Path(f.name)
        os.replace(tmp, self.path)

    def _all(self) -> list[Comment]:
        data = self._read()
        return [Comment.from_dict(c) for c in data.get("comments", [])]

    def _save(self, comments: Iterable[Comment]) -> None:
        self._write({
            "version": STORE_VERSION,
            "comments": [c.to_dict() for c in comments],
        })

    # ----- public API -----

    def list(
        self,
        status: Status | None = None,
        tags: list[str] | None = None,
        include_stale: bool = True,
    ) -> list[Comment]:
        comments = self._all()
        if status is not None:
            comments = [c for c in comments if c.status == status]
        if tags:
            tag_set = set(tags)
            comments = [c for c in comments if tag_set.intersection(c.tags)]
        if not include_stale:
            comments = [c for c in comments if not c.stale]
        return comments

    def get(self, comment_id: str) -> Comment | None:
        for c in self._all():
            if c.id == comment_id:
                return c
        return None

    def add(
        self,
        anchor: Anchor,
        text: str,
        author: Author = "human",
        tags: list[str] | None = None,
        resolved_source: ResolvedSource | None = None,
        snippet: str | None = None,
    ) -> Comment:
        now = _now()
        comment = Comment(
            id=_new_id(),
            anchor=anchor,
            thread=[ThreadEntry(author=author, at=now, text=text)],
            status="open",
            tags=list(tags or []),
            resolved_source=resolved_source,
            snippet=snippet,
            created=now,
            updated=now,
        )
        all_comments = self._all()
        all_comments.append(comment)
        self._save(all_comments)
        return comment

    def _update(
        self, comment_id: str, mutator
    ) -> Comment:
        comments = self._all()
        for i, c in enumerate(comments):
            if c.id == comment_id:
                mutator(c)
                c.updated = _now()
                comments[i] = c
                self._save(comments)
                return c
        raise KeyError(f"comment {comment_id!r} not found")

    def reply(
        self,
        comment_id: str,
        text: str,
        author: Author,
        edits: list[str] | None = None,
    ) -> Comment:
        def mutate(c: Comment) -> None:
            c.thread.append(
                ThreadEntry(author=author, at=_now(), text=text, edits=list(edits or []))
            )
        return self._update(comment_id, mutate)

    def resolve(
        self,
        comment_id: str,
        summary: str,
        edits: list[str] | None = None,
        author: Author = "claude",
    ) -> Comment:
        def mutate(c: Comment) -> None:
            c.thread.append(
                ThreadEntry(author=author, at=_now(), text=summary, edits=list(edits or []))
            )
            c.status = "resolved"
        return self._update(comment_id, mutate)

    def dismiss(
        self,
        comment_id: str,
        reason: str,
        author: Author = "human",
    ) -> Comment:
        def mutate(c: Comment) -> None:
            c.thread.append(
                ThreadEntry(author=author, at=_now(), text=reason)
            )
            c.status = "dismissed"
        return self._update(comment_id, mutate)

    def reopen(self, comment_id: str, author: Author = "human") -> Comment:
        def mutate(c: Comment) -> None:
            c.thread.append(
                ThreadEntry(author=author, at=_now(), text="(reopened)")
            )
            c.status = "open"
            c.stale = False
        return self._update(comment_id, mutate)

    def delete(self, comment_id: str) -> bool:
        comments = self._all()
        before = len(comments)
        comments = [c for c in comments if c.id != comment_id]
        if len(comments) == before:
            return False
        self._save(comments)
        return True

    # ----- staleness -----

    def check_staleness(
        self,
        watch_dir: Path,
        sections_resolver=None,
    ) -> list[str]:
        """Re-check every open comment; mark stale ones, update line ranges.

        ``sections_resolver`` is an optional callable
        ``(title: str, label: str | None) -> ResolvedSource | None`` used
        for SectionAnchor comments (typically wraps :func:`structure.parse_structure`).

        Returns the list of comment IDs that became stale on this pass
        (i.e. were not stale before, but are now).
        """
        comments = self._all()
        newly_stale: list[str] = []
        changed = False

        for c in comments:
            if c.status != "open":
                continue
            was_stale = c.stale
            new_stale = False
            modified = False  # tracks resolved_source updates separate from stale flag

            if c.anchor.kind == "paper":
                # Paper anchors never go stale
                pass

            elif c.anchor.kind == "section":
                anchor = c.anchor  # SectionAnchor
                resolved = None
                if sections_resolver is not None:
                    resolved = sections_resolver(anchor.title, anchor.label)
                if resolved is None:
                    new_stale = True
                elif c.resolved_source != resolved:
                    c.resolved_source = resolved
                    modified = True

            elif c.anchor.kind in ("source_range", "pdf_region"):
                # Use the snippet if present
                resolved = c.resolved_source
                if c.snippet and resolved is not None:
                    file_path = watch_dir / resolved.file
                    if file_path.is_file():
                        located = find_snippet(c.snippet, file_path)
                        if located is None:
                            new_stale = True
                        else:
                            ls, le = located
                            if (resolved.line_start, resolved.line_end) != (ls, le):
                                c.resolved_source = ResolvedSource(
                                    file=resolved.file,
                                    line_start=ls,
                                    line_end=le,
                                    excerpt=resolved.excerpt,
                                )
                                modified = True
                    else:
                        new_stale = True
                # without a snippet we can't verify; leave alone

            if new_stale != was_stale:
                c.stale = new_stale
                modified = True
                if new_stale:
                    newly_stale.append(c.id)

            if modified:
                changed = True

        if changed:
            self._save(comments)

        return newly_stale

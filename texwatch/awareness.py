"""User awareness tracking for the texwatch MCP context channel.

Tracks what the user is looking at (cursor, selection, visible lines,
PDF viewport) and manages highlights/annotations that Claude Code
can push to the editor.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class UserFocus:
    """Current user focus state, updated from WebSocket messages."""

    file: str | None = None
    cursor: tuple[int, int] | None = None
    selection_start: tuple[int, int] | None = None
    selection_end: tuple[int, int] | None = None
    visible_lines: tuple[int, int] | None = None
    pdf_page: int | None = None
    pdf_scroll_y: float | None = None
    timestamp: str | None = None
    ws_connected: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, omitting None fields."""
        d: dict[str, Any] = {}
        if self.file is not None:
            d["file"] = self.file
        if self.cursor is not None:
            d["cursor"] = {"line": self.cursor[0], "col": self.cursor[1]}
        if self.selection_start is not None and self.selection_end is not None:
            d["selection"] = {
                "start": {"line": self.selection_start[0], "col": self.selection_start[1]},
                "end": {"line": self.selection_end[0], "col": self.selection_end[1]},
            }
        if self.visible_lines is not None:
            d["visible_lines"] = list(self.visible_lines)
        if self.pdf_page is not None:
            d["pdf_page"] = self.pdf_page
        d["ws_connected"] = self.ws_connected
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp
        return d


def _safe_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def update_focus(focus: UserFocus, msg: dict) -> None:
    """Update UserFocus from a WebSocket message. Invalid fields silently ignored."""
    msg_type = msg.get("type")
    now = datetime.now(timezone.utc).isoformat()
    focus.timestamp = now
    focus.ws_connected = True

    if msg_type == "focus":
        file_val = msg.get("file")
        if isinstance(file_val, str):
            focus.file = file_val
        line = _safe_int(msg.get("line"))
        col = _safe_int(msg.get("column"))
        if line is not None and col is not None:
            focus.cursor = (line, col)

    elif msg_type == "selection":
        file_val = msg.get("file")
        if isinstance(file_val, str):
            focus.file = file_val
        start = msg.get("start", {})
        end = msg.get("end", {})
        s_line = _safe_int(start.get("line"))
        s_col = _safe_int(start.get("col"))
        e_line = _safe_int(end.get("line"))
        e_col = _safe_int(end.get("col"))
        if all(v is not None for v in (s_line, s_col, e_line, e_col)):
            focus.selection_start = (s_line, s_col)
            focus.selection_end = (e_line, e_col)

    elif msg_type == "visible_lines":
        file_val = msg.get("file")
        if isinstance(file_val, str):
            focus.file = file_val
        start = _safe_int(msg.get("start"))
        end = _safe_int(msg.get("end"))
        if start is not None and end is not None:
            focus.visible_lines = (start, end)

    elif msg_type == "pdf_viewport":
        page = _safe_int(msg.get("page"))
        scroll_y = _safe_float(msg.get("scroll_y"))
        if page is not None:
            focus.pdf_page = page
        if scroll_y is not None:
            focus.pdf_scroll_y = scroll_y


def on_ws_disconnect(focus: UserFocus) -> None:
    """Mark the user as disconnected."""
    focus.ws_connected = False
    focus.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class HighlightState:
    """Active editor highlights, keyed by file."""
    highlights: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def set_highlights(self, file: str, ranges: list[dict[str, Any]]) -> dict:
        """Set highlights for a file. Returns WebSocket broadcast payload."""
        self.highlights[file] = ranges
        return {"type": "highlights", "file": file, "ranges": ranges}

    def clear_file(self, file: str) -> dict:
        """Clear highlights for a file."""
        self.highlights.pop(file, None)
        return {"type": "highlights", "file": file, "ranges": []}


@dataclass
class AnnotationState:
    """Active gutter annotations, keyed by file."""
    annotations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def set_annotations(self, file: str, annotations: list[dict[str, Any]]) -> dict:
        """Set annotations for a file. Returns WebSocket broadcast payload."""
        self.annotations[file] = annotations
        return {"type": "annotations", "file": file, "annotations": annotations}

    def clear_all(self) -> list[dict]:
        """Clear all annotations (called on successful recompile)."""
        payloads = []
        for file in list(self.annotations.keys()):
            payloads.append({"type": "annotations", "file": file, "annotations": []})
        self.annotations.clear()
        return payloads


def resolve_viewport_capture(focus: UserFocus) -> int:
    """Determine page number for viewport capture. Returns 1 if unknown/stale."""
    if focus.ws_connected and focus.pdf_page is not None:
        return focus.pdf_page
    return 1

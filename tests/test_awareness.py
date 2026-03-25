"""Tests for awareness module."""

import pytest

from texwatch.awareness import (
    UserFocus, HighlightState, AnnotationState,
    update_focus, on_ws_disconnect, resolve_viewport_capture,
)


class TestUserFocus:
    def test_default_state(self):
        focus = UserFocus()
        assert focus.file is None
        assert focus.cursor is None
        assert focus.ws_connected is False

    def test_update_focus_cursor(self):
        focus = UserFocus()
        update_focus(focus, {"type": "focus", "file": "main.tex", "line": 10, "column": 5})
        assert focus.file == "main.tex"
        assert focus.cursor == (10, 5)
        assert focus.ws_connected is True

    def test_update_focus_selection(self):
        focus = UserFocus()
        update_focus(focus, {"type": "selection", "file": "main.tex",
            "start": {"line": 5, "col": 0}, "end": {"line": 10, "col": 0}})
        assert focus.selection_start == (5, 0)
        assert focus.selection_end == (10, 0)

    def test_update_focus_visible_lines(self):
        focus = UserFocus()
        update_focus(focus, {"type": "visible_lines", "file": "main.tex", "start": 1, "end": 40})
        assert focus.visible_lines == (1, 40)

    def test_update_focus_pdf_viewport(self):
        focus = UserFocus()
        update_focus(focus, {"type": "pdf_viewport", "page": 3, "scroll_y": 0.5})
        assert focus.pdf_page == 3
        assert focus.pdf_scroll_y == 0.5

    def test_update_focus_ignores_malformed_ints(self):
        focus = UserFocus()
        update_focus(focus, {"type": "focus", "file": "main.tex", "line": "bad", "column": 5})
        assert focus.cursor is None

    def test_update_focus_preserves_previous_on_missing_fields(self):
        focus = UserFocus()
        update_focus(focus, {"type": "focus", "file": "main.tex", "line": 10, "column": 5})
        update_focus(focus, {"type": "pdf_viewport", "page": 2, "scroll_y": 0.0})
        assert focus.cursor == (10, 5)
        assert focus.pdf_page == 2

    def test_update_sets_timestamp(self):
        focus = UserFocus()
        update_focus(focus, {"type": "focus", "file": "main.tex", "line": 1, "column": 0})
        assert focus.timestamp is not None

    def test_to_dict_omits_none(self):
        focus = UserFocus()
        update_focus(focus, {"type": "focus", "file": "main.tex", "line": 1, "column": 0})
        d = focus.to_dict()
        assert "file" in d
        assert "cursor" in d
        assert "selection" not in d

    def test_on_ws_disconnect(self):
        focus = UserFocus()
        update_focus(focus, {"type": "focus", "file": "main.tex", "line": 1, "column": 0})
        assert focus.ws_connected is True
        on_ws_disconnect(focus)
        assert focus.ws_connected is False
        assert focus.timestamp is not None


class TestHighlightState:
    def test_set_highlights(self):
        state = HighlightState()
        payload = state.set_highlights("main.tex", [{"start": 1, "end": 5, "color": "yellow"}])
        assert payload["type"] == "highlights"
        assert payload["file"] == "main.tex"
        assert len(payload["ranges"]) == 1
        assert state.highlights["main.tex"] == [{"start": 1, "end": 5, "color": "yellow"}]

    def test_clear_file(self):
        state = HighlightState()
        state.set_highlights("main.tex", [{"start": 1, "end": 5, "color": "yellow"}])
        payload = state.clear_file("main.tex")
        assert payload["ranges"] == []
        assert "main.tex" not in state.highlights


class TestAnnotationState:
    def test_set_annotations(self):
        state = AnnotationState()
        payload = state.set_annotations("main.tex", [{"line": 10, "type": "warning", "text": "test"}])
        assert payload["type"] == "annotations"
        assert len(payload["annotations"]) == 1

    def test_clear_all(self):
        state = AnnotationState()
        state.set_annotations("a.tex", [{"line": 1, "type": "error", "text": "err"}])
        state.set_annotations("b.tex", [{"line": 2, "type": "warning", "text": "warn"}])
        payloads = state.clear_all()
        assert len(payloads) == 2
        assert state.annotations == {}


class TestResolveViewportCapture:
    def test_returns_current_page_when_connected(self):
        focus = UserFocus(pdf_page=5, ws_connected=True)
        assert resolve_viewport_capture(focus) == 5

    def test_returns_1_when_disconnected(self):
        focus = UserFocus(pdf_page=5, ws_connected=False)
        assert resolve_viewport_capture(focus) == 1

    def test_returns_1_when_no_page(self):
        focus = UserFocus(ws_connected=True)
        assert resolve_viewport_capture(focus) == 1

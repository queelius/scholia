"""Tests for server module."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

from texwatch.config import Config
from texwatch.compiler import CompileMessage, CompileResult
from texwatch.server import TexWatchServer


@pytest.fixture
def config(tmp_path):
    """Create a test configuration."""
    main_file = tmp_path / "main.tex"
    main_file.write_text("\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n")

    return Config(
        main="main.tex",
        watch=["*.tex"],
        ignore=[],
        compiler="latexmk",
        port=0,  # Use any available port
        config_path=tmp_path / ".texwatch.yaml",
    )


@pytest.fixture
def server(config):
    """Create a TexWatchServer instance."""
    return TexWatchServer(config)


@pytest.fixture
async def client(server):
    """Create an aiohttp test client."""
    async with TestClient(TestServer(server.app)) as client:
        yield client


class TestStatusEndpoint:
    """Tests for GET /status endpoint."""

    @pytest.mark.asyncio
    async def test_status_initial(self, client):
        """Test initial status response."""
        resp = await client.get("/status")
        assert resp.status == 200

        data = await resp.json()
        assert "file" in data
        assert "compiling" in data
        assert data["compiling"] is False
        assert "errors" in data
        assert "warnings" in data
        assert "viewer" in data

    @pytest.mark.asyncio
    async def test_status_includes_page_limit(self, client, server):
        """Test /status response includes page_limit field."""
        resp = await client.get("/status")
        data = await resp.json()
        assert "page_limit" in data
        # Default config has page_limit=None
        assert data["page_limit"] is None

    @pytest.mark.asyncio
    async def test_status_includes_total_pages(self, client, server):
        """Test /status response includes total_pages field."""
        resp = await client.get("/status")
        data = await resp.json()
        assert "total_pages" in data
        assert data["total_pages"] == 0  # initial value

    @pytest.mark.asyncio
    async def test_status_page_limit_when_set(self, client, server):
        """Test /status response includes page_limit when config has a value."""
        server._single.config.page_limit = 42
        resp = await client.get("/status")
        data = await resp.json()
        assert data["page_limit"] == 42

    @pytest.mark.asyncio
    async def test_status_after_compile(self, client, server):
        """Test status after a compile result is set."""
        server._last_result = CompileResult(
            success=True,
            errors=[],
            warnings=[
                CompileMessage(file="main.tex", line=42, message="Underfull hbox", type="warning"),
            ],
        )

        resp = await client.get("/status")
        data = await resp.json()
        assert data["success"] is True
        assert len(data["warnings"]) == 1
        assert data["warnings"][0]["line"] == 42


class TestConfigEndpoint:
    """Tests for GET /config endpoint."""

    @pytest.mark.asyncio
    async def test_config(self, client, config):
        """Test config endpoint returns configuration."""
        resp = await client.get("/config")
        assert resp.status == 200

        data = await resp.json()
        assert data["main"] == "main.tex"
        assert data["compiler"] == "latexmk"
        assert data["port"] == 0


    @pytest.mark.asyncio
    async def test_config_includes_page_limit_when_set(self, client, config):
        """Test /config includes page_limit when config has a value."""
        # Modify the config to have a page_limit
        config.page_limit = 100
        resp = await client.get("/config")
        assert resp.status == 200
        data = await resp.json()
        assert data["page_limit"] == 100

    @pytest.mark.asyncio
    async def test_config_omits_page_limit_when_none(self, client, config):
        """Test /config omits page_limit when config has None."""
        config.page_limit = None
        resp = await client.get("/config")
        assert resp.status == 200
        data = await resp.json()
        assert "page_limit" not in data


class TestGotoEndpoint:
    """Tests for POST /goto endpoint."""

    @pytest.mark.asyncio
    async def test_goto_line_no_synctex(self, client, server):
        """Test goto line without SyncTeX data - fallback returns success."""
        # With no synctex and no total_pages, the last-resort fallback kicks in
        resp = await client.post(
            "/goto",
            json={"line": 42},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data.get("estimated") is True

    @pytest.mark.asyncio
    async def test_goto_page(self, client, server):
        """Test goto page broadcasts to websockets."""
        resp = await client.post(
            "/goto",
            json={"page": 3},
        )
        assert resp.status == 200

        data = await resp.json()
        assert data["success"] is True
        assert data["page"] == 3

    @pytest.mark.asyncio
    async def test_goto_invalid_json(self, client):
        """Test goto with invalid JSON."""
        resp = await client.post(
            "/goto",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_goto_missing_target(self, client):
        """Test goto without line, page, or section."""
        resp = await client.post(
            "/goto",
            json={"invalid": "data"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_goto_section_no_match(self, client):
        """Test goto section returns 404 when no section matches."""
        resp = await client.post(
            "/goto",
            json={"section": "Introduction"},
        )
        assert resp.status == 404
        data = await resp.json()
        assert "No section matching" in data["error"]

    @pytest.mark.asyncio
    async def test_goto_line_fallback_with_pages(self, client, server):
        """Test goto line fallback estimates page from line count."""
        # Set viewer state with total_pages known
        server._viewer_state["total_pages"] = 10

        # The main.tex has 4 lines, requesting line 2
        # estimated_page = round(2/4 * 10) = round(5.0) = 5
        resp = await client.post(
            "/goto",
            json={"line": 2},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True
        assert data["page"] == 5

    @pytest.mark.asyncio
    async def test_goto_line_fallback_no_pages(self, client, server):
        """Test goto line fallback when total_pages=0 - last resort."""
        server._viewer_state["total_pages"] = 0

        resp = await client.post(
            "/goto",
            json={"line": 1},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True

    @pytest.mark.asyncio
    async def test_goto_line_last_resort_broadcasts_page(self, client, server):
        """Test last-resort goto broadcasts page=1 (not line)."""
        server._viewer_state["total_pages"] = 0
        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state
            resp = await client.post("/goto", json={"line": 42})
            assert resp.status == 200
            msg = await ws.receive_json()
            assert msg["type"] == "goto"
            assert msg["page"] == 1
            assert "line" not in msg

    @pytest.mark.asyncio
    async def test_goto_line_estimation_clamped(self, client, server):
        """Test that estimated page is clamped to total_pages."""
        server._viewer_state["total_pages"] = 5

        # Request line 9999 (way beyond the 4-line file)
        # estimated = round(9999/4 * 5) which is huge, but clamped to 5
        resp = await client.post(
            "/goto",
            json={"line": 9999},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["page"] <= 5


    @pytest.mark.asyncio
    async def test_goto_line_synctex_full_position(self, client, server, config):
        """Test goto line with SyncTeX broadcasts full bounding box."""
        from texwatch.synctex import PDFPosition, SyncTeXData

        synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [
                    PDFPosition(page=1, x=72.0, y=600.0, width=200.0, height=12.0)
                ],
            },
            input_files={},
        )
        server._synctex_data = synctex_data

        async with client.ws_connect("/ws") as ws:
            # Receive initial state
            await ws.receive_json()

            # Trigger goto
            resp = await client.post("/goto", json={"line": 10})
            assert resp.status == 200

            # Should receive goto broadcast with full position
            msg = await ws.receive_json()
            assert msg["type"] == "goto"
            assert msg["page"] == 1
            assert msg["x"] == 72.0
            assert msg["y"] == 600.0
            assert msg["width"] == 200.0
            assert msg["height"] == 12.0

    @pytest.mark.asyncio
    async def test_goto_line_synctex_zero_dimensions(self, client, server, config):
        """Test goto with zero-dimension SyncTeX still sends fields."""
        from texwatch.synctex import PDFPosition, SyncTeXData

        synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 5): [
                    PDFPosition(page=1, x=72.0, y=400.0, width=0.0, height=0.0)
                ],
            },
            input_files={},
        )
        server._synctex_data = synctex_data

        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()

            resp = await client.post("/goto", json={"line": 5})
            assert resp.status == 200

            msg = await ws.receive_json()
            assert msg["type"] == "goto"
            assert msg["x"] == 72.0
            assert msg["y"] == 400.0
            assert msg["width"] == 0.0
            assert msg["height"] == 0.0


class TestCountSourceLines:
    """Tests for _count_source_lines helper."""

    def test_count_source_lines(self, server, config):
        """Test counting lines in the main tex file."""
        main_file = config.config_path.parent / "main.tex"
        count = server._count_source_lines(main_file)
        assert count == 4  # The fixture creates a 4-line file

    def test_count_source_lines_missing(self, server):
        """Test counting lines in a non-existent file."""
        count = server._count_source_lines(Path("/nonexistent/file.tex"))
        assert count == 0

    def test_count_source_lines_empty(self, server, tmp_path):
        """Test counting lines in an empty file."""
        empty = tmp_path / "empty.tex"
        empty.write_text("")
        count = server._count_source_lines(empty)
        # An empty file has 0 lines when split
        assert count == 0


class TestCompileEndpoint:
    """Tests for POST /compile endpoint."""

    @pytest.mark.asyncio
    async def test_compile_already_running(self, client, server):
        """Test compile when already compiling."""
        server._compiling = True
        resp = await client.post("/compile")
        assert resp.status == 409

    @pytest.mark.asyncio
    async def test_compile_triggers_build(self, client, server):
        """Test compile endpoint triggers compilation."""
        with patch("texwatch.server.compile_tex") as mock_compile:
            mock_compile.return_value = CompileResult(success=True)
            resp = await client.post("/compile")
            assert resp.status == 200
            data = await resp.json()
            assert data["success"] is True


class TestCompiledBroadcastLogOutput:
    """Tests for log_output in the compiled WebSocket broadcast."""

    @pytest.mark.asyncio
    async def test_compiled_broadcast_includes_log_output(self, client, server):
        """Test that compiled broadcast includes log_output when present."""
        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state

            with patch("texwatch.server.compile_tex") as mock_compile:
                mock_compile.return_value = CompileResult(
                    success=True,
                    log_output="This is a test log\nLine 2\n",
                )
                await client.post("/compile")

            # Receive compiling=True, compiling=False, then compiled
            messages = []
            for _ in range(3):
                msg = await ws.receive_json()
                messages.append(msg)

            compiled_msg = next(m for m in messages if m["type"] == "compiled")
            assert "log_output" in compiled_msg
            assert "This is a test log" in compiled_msg["log_output"]

    @pytest.mark.asyncio
    async def test_compiled_broadcast_omits_log_output_when_empty(self, client, server):
        """Test that compiled broadcast omits log_output when empty."""
        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state

            with patch("texwatch.server.compile_tex") as mock_compile:
                mock_compile.return_value = CompileResult(
                    success=True,
                    log_output="",  # empty
                )
                await client.post("/compile")

            # Receive compiling=True, compiling=False, then compiled
            messages = []
            for _ in range(3):
                msg = await ws.receive_json()
                messages.append(msg)

            compiled_msg = next(m for m in messages if m["type"] == "compiled")
            assert "log_output" not in compiled_msg


class TestCaptureEndpoint:
    """Tests for GET /capture endpoint."""

    @pytest.mark.asyncio
    async def test_capture_no_pdf(self, client, server):
        """Test capture when no PDF exists."""
        resp = await client.get("/capture")
        assert resp.status == 404
        data = await resp.json()
        assert "PDF not found" in data["error"]

    @pytest.mark.asyncio
    async def test_capture_success(self, client, server, config):
        """Test capture produces PNG from a real PDF."""
        import pymupdf

        # Create a test PDF using pymupdf
        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Hello, test!")
        doc.save(str(pdf_path))
        doc.close()

        resp = await client.get("/capture")
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/png"
        data = await resp.read()
        # Verify PNG signature
        assert data[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_capture_specific_page(self, client, server, config):
        """Test capture with ?page=2."""
        import pymupdf

        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        resp = await client.get("/capture?page=2")
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/png"

    @pytest.mark.asyncio
    async def test_capture_invalid_page(self, client, server, config):
        """Test capture with ?page=abc returns 400."""
        import pymupdf

        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        resp = await client.get("/capture?page=abc")
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid page" in data["error"]

    @pytest.mark.asyncio
    async def test_capture_out_of_range(self, client, server, config):
        """Test capture with ?page=999 returns 400."""
        import pymupdf

        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        resp = await client.get("/capture?page=999")
        assert resp.status == 400
        data = await resp.json()
        assert "out of range" in data["error"]

    @pytest.mark.asyncio
    async def test_capture_custom_dpi(self, client, server, config):
        """Test capture with ?dpi=72 produces smaller image."""
        import pymupdf

        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        resp_low = await client.get("/capture?dpi=72")
        assert resp_low.status == 200
        data_low = await resp_low.read()

        resp_high = await client.get("/capture?dpi=300")
        assert resp_high.status == 200
        data_high = await resp_high.read()

        # Higher DPI should produce more bytes
        assert len(data_high) > len(data_low)

    @pytest.mark.asyncio
    async def test_capture_invalid_dpi(self, client, server, config):
        """Test capture with ?dpi=abc returns 400."""
        import pymupdf

        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        resp = await client.get("/capture?dpi=abc")
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid dpi" in data["error"]

    @pytest.mark.asyncio
    async def test_capture_no_pymupdf(self, client, server, config):
        """Test capture when pymupdf is not installed returns 501."""
        # Create a PDF so we get past the "not found" check
        pdf_path = config.config_path.parent / "main.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        # Simulate pymupdf not being available
        with patch.dict(sys.modules, {"pymupdf": None}):
            resp = await client.get("/capture")
            assert resp.status == 501
            data = await resp.json()
            assert "pymupdf not installed" in data["error"]
            assert "pip install" in data["error"]


class TestPdfEndpoint:
    """Tests for GET /pdf endpoint."""

    @pytest.mark.asyncio
    async def test_pdf_not_found(self, client):
        """Test PDF endpoint when no PDF exists."""
        resp = await client.get("/pdf")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_pdf_exists(self, client, config):
        """Test PDF endpoint when PDF exists."""
        # Create a fake PDF file
        pdf_path = config.config_path.parent / "main.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")

        resp = await client.get("/pdf")
        assert resp.status == 200
        assert "pdf" in resp.headers.get("Content-Type", "")


class TestIndexEndpoint:
    """Tests for GET / endpoint."""

    @pytest.mark.asyncio
    async def test_index(self, client):
        """Test index page is served."""
        resp = await client.get("/")
        assert resp.status == 200
        content = await resp.text()
        assert "texwatch" in content


class TestWebSocket:
    """Tests for WebSocket endpoint."""

    @pytest.mark.asyncio
    async def test_websocket_connect(self, client, server):
        """Test WebSocket connection."""
        async with client.ws_connect("/ws") as ws:
            # Should receive initial state
            msg = await ws.receive_json()
            assert msg["type"] == "state"
            assert "compiling" in msg
            assert len(server._websockets) == 1

        # After disconnect
        # Give a moment for cleanup
        import asyncio
        await asyncio.sleep(0.1)
        assert len(server._websockets) == 0

    @pytest.mark.asyncio
    async def test_websocket_viewer_state(self, client, server):
        """Test WebSocket viewer state update."""
        async with client.ws_connect("/ws") as ws:
            # Receive initial state
            await ws.receive_json()

            # Send viewer state update
            await ws.send_json({
                "type": "viewer_state",
                "state": {
                    "page": 5,
                    "total_pages": 10,
                }
            })

            # Give server time to process
            import asyncio
            await asyncio.sleep(0.1)

            assert server._viewer_state["page"] == 5
            assert server._viewer_state["total_pages"] == 10


class TestEditorState:
    """Tests for editor state tracking."""

    def test_initial_editor_state_is_null(self, server):
        """Test editor state defaults to null values."""
        assert server._editor_state["file"] is None
        assert server._editor_state["line"] is None

    @pytest.mark.asyncio
    async def test_editor_state_update_via_websocket(self, client, server):
        """Test editor state updates when receiving editor_state WebSocket message."""
        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state

            await ws.send_json({
                "type": "editor_state",
                "state": {
                    "file": "chapters/intro.tex",
                    "line": 42,
                }
            })

            import asyncio
            await asyncio.sleep(0.1)

            assert server._editor_state["file"] == "chapters/intro.tex"
            assert server._editor_state["line"] == 42

    @pytest.mark.asyncio
    async def test_status_includes_editor_state(self, client, server):
        """Test /status response includes editor state."""
        server._editor_state = {"file": "main.tex", "line": 10}

        resp = await client.get("/status")
        data = await resp.json()
        assert "editor" in data
        assert data["editor"]["file"] == "main.tex"
        assert data["editor"]["line"] == 10

    @pytest.mark.asyncio
    async def test_initial_ws_state_includes_editor(self, client, server):
        """Test initial WebSocket state message includes editor state."""
        server._editor_state = {"file": "test.tex", "line": 5}

        async with client.ws_connect("/ws") as ws:
            msg = await ws.receive_json()
            assert msg["type"] == "state"
            assert "editor" in msg
            assert msg["editor"]["file"] == "test.tex"
            assert msg["editor"]["line"] == 5


class TestServerHelpers:
    """Tests for server helper methods."""

    def test_result_to_dict(self, server):
        """Test CompileResult serialization."""
        result = CompileResult(
            success=False,
            errors=[CompileMessage(file="test.tex", line=10, message="err", type="error")],
            warnings=[CompileMessage(file="test.tex", line=20, message="warn", type="warning")],
            duration_seconds=1.5,
        )

        d = server._result_to_dict(result)
        assert d["success"] is False
        assert len(d["errors"]) == 1
        assert len(d["warnings"]) == 1
        assert d["duration_seconds"] == 1.5
        assert "timestamp" in d

    def test_message_to_dict(self, server):
        """Test CompileMessage serialization."""
        msg = CompileMessage(file="main.tex", line=42, message="Test error", type="error")
        d = server._message_to_dict(msg)
        assert d["file"] == "main.tex"
        assert d["line"] == 42
        assert d["message"] == "Test error"
        assert d["type"] == "error"


class TestGetSourceEndpoint:
    """Tests for GET /source endpoint."""

    @pytest.mark.asyncio
    async def test_get_source_success(self, client, config):
        """Test GET /source returns content and mtime_ns as string."""
        resp = await client.get("/source?file=main.tex")
        assert resp.status == 200

        data = await resp.json()
        assert data["file"] == "main.tex"
        assert "\\documentclass{article}" in data["content"]
        assert "mtime_ns" in data
        # mtime_ns must be a string (to preserve precision for JS)
        assert isinstance(data["mtime_ns"], str)
        assert len(data["mtime_ns"]) > 0

    @pytest.mark.asyncio
    async def test_get_source_default_file(self, client, config):
        """Test GET /source without ?file= returns main file."""
        resp = await client.get("/source")
        assert resp.status == 200

        data = await resp.json()
        assert data["file"] == "main.tex"
        assert "\\documentclass{article}" in data["content"]

    @pytest.mark.asyncio
    async def test_get_source_not_found(self, client):
        """Test GET /source for nonexistent file returns 404."""
        resp = await client.get("/source?file=nonexistent.tex")
        assert resp.status == 404

        data = await resp.json()
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_get_source_path_traversal(self, client):
        """Test GET /source rejects path traversal attempts."""
        resp = await client.get("/source?file=../../etc/passwd")
        assert resp.status == 403

        data = await resp.json()
        assert "denied" in data["error"].lower()


class TestPostSourceEndpoint:
    """Tests for POST /source endpoint."""

    @pytest.mark.asyncio
    async def test_save_source_success(self, client, config):
        """Test saving with valid base_mtime_ns succeeds."""
        # First, get the current mtime
        get_resp = await client.get("/source?file=main.tex")
        get_data = await get_resp.json()
        mtime_ns = get_data["mtime_ns"]

        # Save with matching base_mtime_ns
        new_content = "\\documentclass{article}\n\\begin{document}\nUpdated!\n\\end{document}\n"
        resp = await client.post("/source", json={
            "file": "main.tex",
            "content": new_content,
            "base_mtime_ns": mtime_ns,
        })
        assert resp.status == 200

        data = await resp.json()
        assert data["success"] is True
        assert "mtime_ns" in data
        assert isinstance(data["mtime_ns"], str)
        # New mtime should differ from old
        assert data["mtime_ns"] != mtime_ns

        # Verify the file was actually written
        file_path = config.config_path.parent / "main.tex"
        assert "Updated!" in file_path.read_text()

    @pytest.mark.asyncio
    async def test_save_source_conflict(self, client, config):
        """Test saving with stale base_mtime_ns returns 409."""
        resp = await client.post("/source", json={
            "file": "main.tex",
            "content": "new content",
            "base_mtime_ns": "0",  # stale mtime
        })
        assert resp.status == 409

        data = await resp.json()
        assert "modified externally" in data["error"].lower()
        assert "current_mtime_ns" in data

    @pytest.mark.asyncio
    async def test_save_source_no_mtime_check(self, client, config):
        """Test saving without base_mtime_ns succeeds (force save)."""
        new_content = "\\documentclass{article}\n\\begin{document}\nForced!\n\\end{document}\n"
        resp = await client.post("/source", json={
            "file": "main.tex",
            "content": new_content,
        })
        assert resp.status == 200

        data = await resp.json()
        assert data["success"] is True

        file_path = config.config_path.parent / "main.tex"
        assert "Forced!" in file_path.read_text()

    @pytest.mark.asyncio
    async def test_save_source_path_traversal(self, client):
        """Test POST /source rejects path traversal."""
        resp = await client.post("/source", json={
            "file": "../../etc/passwd",
            "content": "malicious",
        })
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_save_source_missing_fields(self, client):
        """Test POST /source with missing fields returns 400."""
        resp = await client.post("/source", json={
            "file": "main.tex",
            # missing "content"
        })
        assert resp.status == 400

        data = await resp.json()
        assert "missing" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_save_source_not_found(self, client):
        """Test POST /source for nonexistent file returns 404."""
        resp = await client.post("/source", json={
            "file": "nonexistent.tex",
            "content": "content",
        })
        assert resp.status == 404


class TestFilesEndpoint:
    """Tests for GET /files endpoint."""

    @pytest.mark.asyncio
    async def test_files_basic(self, client, config):
        """Test /files returns tree with main.tex."""
        resp = await client.get("/files")
        assert resp.status == 200

        data = await resp.json()
        assert "root" in data
        assert "children" in data
        names = [c["name"] for c in data["children"]]
        assert "main.tex" in names

    @pytest.mark.asyncio
    async def test_files_includes_bib(self, client, config):
        """Test /files includes .bib files."""
        bib_path = config.config_path.parent / "refs.bib"
        bib_path.write_text("@article{test, title={Test}}")

        resp = await client.get("/files")
        data = await resp.json()
        names = [c["name"] for c in data["children"]]
        assert "refs.bib" in names

    @pytest.mark.asyncio
    async def test_files_excludes_non_relevant(self, client, config):
        """Test /files excludes non-relevant files like .jpg."""
        jpg_path = config.config_path.parent / "image.jpg"
        jpg_path.write_bytes(b"\xff\xd8\xff")

        resp = await client.get("/files")
        data = await resp.json()
        names = [c["name"] for c in data["children"]]
        assert "image.jpg" not in names

    @pytest.mark.asyncio
    async def test_files_nested_directory(self, client, config):
        """Test /files includes nested directories with .tex files."""
        chapters = config.config_path.parent / "chapters"
        chapters.mkdir()
        (chapters / "intro.tex").write_text("\\chapter{Intro}")

        resp = await client.get("/files")
        data = await resp.json()
        dir_names = [c["name"] for c in data["children"] if c["type"] == "directory"]
        assert "chapters" in dir_names

        # Find chapters dir and check children
        chapters_node = next(c for c in data["children"] if c["name"] == "chapters")
        child_names = [c["name"] for c in chapters_node["children"]]
        assert "intro.tex" in child_names

    @pytest.mark.asyncio
    async def test_files_excludes_hidden(self, client, config):
        """Test /files excludes hidden directories."""
        hidden = config.config_path.parent / ".hidden"
        hidden.mkdir()
        (hidden / "secret.tex").write_text("secret")

        resp = await client.get("/files")
        data = await resp.json()
        names = [c["name"] for c in data["children"]]
        assert ".hidden" not in names

    @pytest.mark.asyncio
    async def test_files_empty_dirs_excluded(self, client, config):
        """Test /files excludes dirs with only irrelevant files."""
        images = config.config_path.parent / "images"
        images.mkdir()
        (images / "photo.png").write_bytes(b"\x89PNG")

        resp = await client.get("/files")
        data = await resp.json()
        dir_names = [c["name"] for c in data["children"] if c["type"] == "directory"]
        assert "images" not in dir_names

    @pytest.mark.asyncio
    async def test_files_paths_relative(self, client, config):
        """Test /files returns relative paths."""
        chapters = config.config_path.parent / "chapters"
        chapters.mkdir()
        (chapters / "intro.tex").write_text("\\chapter{Intro}")

        resp = await client.get("/files")
        data = await resp.json()
        chapters_node = next(c for c in data["children"] if c["name"] == "chapters")
        intro = chapters_node["children"][0]
        # Path should be relative: "chapters/intro.tex"
        assert intro["path"] == "chapters/intro.tex"
        assert "/" in intro["path"]
        assert not intro["path"].startswith("/")


class TestSourceUpdatedBroadcast:
    """Tests for source_updated WebSocket broadcast."""

    @pytest.mark.asyncio
    async def test_file_change_broadcasts_source_updated(self, client, server, config):
        """Test that _on_file_change broadcasts source_updated message."""
        import asyncio

        async with client.ws_connect("/ws") as ws:
            # Receive initial state
            await ws.receive_json()

            # Trigger file change with the main.tex path
            main_path = str(config.config_path.parent / "main.tex")
            # Mock _do_compile to avoid actual compilation
            with patch.object(server, "_do_compile", new_callable=AsyncMock):
                await server._on_file_change(main_path)

            # Should receive source_updated broadcast
            msg = await ws.receive_json()
            assert msg["type"] == "source_updated"
            assert msg["file"] == "main.tex"
            assert "mtime_ns" in msg
            assert isinstance(msg["mtime_ns"], str)


class TestServerLogging:
    """Tests for debug logging in server handlers."""

    @pytest.mark.asyncio
    async def test_goto_line_synctex_hit_logs(self, client, server, caplog):
        """Test that goto with synctex hit logs correctly."""
        from texwatch.synctex import PDFPosition, SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [
                    PDFPosition(page=1, x=72.0, y=600.0, width=200.0, height=12.0)
                ],
            },
            input_files={},
        )

        with caplog.at_level(logging.DEBUG, logger="texwatch.server"):
            resp = await client.post("/goto", json={"line": 10})
            assert resp.status == 200

        assert "synctex hit" in caplog.text

    @pytest.mark.asyncio
    async def test_goto_line_fallback_logs(self, client, server, caplog):
        """Test that goto without synctex logs fallback."""
        server._synctex_data = None

        with caplog.at_level(logging.DEBUG, logger="texwatch.server"):
            resp = await client.post("/goto", json={"line": 10})
            assert resp.status == 200

        assert "synctex miss" in caplog.text or "synctex_data=None" in caplog.text

    @pytest.mark.asyncio
    async def test_reverse_sync_click_logs(self, client, server, caplog):
        """Test that reverse sync click logs the chain."""
        from texwatch.synctex import PDFPosition, SourcePosition, SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={
                1: [(600.0, SourcePosition(file="main.tex", line=10))],
            },
            source_to_pdf={},
            input_files={},
        )

        with caplog.at_level(logging.DEBUG, logger="texwatch.server"):
            async with client.ws_connect("/ws") as ws:
                await ws.receive_json()  # initial state
                await ws.send_json({
                    "type": "click",
                    "page": 1,
                    "x": 100.0,
                    "y": 600.0,
                })
                msg = await ws.receive_json()
                assert msg["type"] == "source_position"

        assert "reverse-sync:" in caplog.text


class TestPortConflict:
    """Tests for port-already-in-use handling."""

    def test_run_raises_system_exit_on_port_conflict(self, config):
        """Test that run() raises SystemExit(1) when the port is already bound."""
        import errno as _errno

        err = OSError(f"[Errno {_errno.EADDRINUSE}] Address already in use")
        err.errno = _errno.EADDRINUSE

        server = TexWatchServer(config)
        with patch("asyncio.run", side_effect=err):
            with pytest.raises(SystemExit) as exc_info:
                server.run(port=9999)
            assert exc_info.value.code == 1

    def test_run_prints_friendly_message_on_port_conflict(self, config, capsys):
        """Test that run() prints a helpful message when the port is in use."""
        import errno as _errno

        err = OSError(f"[Errno {_errno.EADDRINUSE}] Address already in use")
        err.errno = _errno.EADDRINUSE

        server = TexWatchServer(config)
        with patch("asyncio.run", side_effect=err):
            with pytest.raises(SystemExit):
                server.run(port=9999)

        captured = capsys.readouterr()
        assert "already in use" in captured.out
        assert "another texwatch instance" in captured.out.lower()
        assert "9999" in captured.out

    def test_run_reraises_other_oserror(self, config):
        """Test that run() re-raises OSErrors that are not EADDRINUSE."""
        err = OSError("Permission denied")
        err.errno = 13  # EACCES

        server = TexWatchServer(config)
        with patch("asyncio.run", side_effect=err):
            with pytest.raises(OSError, match="Permission denied"):
                server.run(port=9999)


# ───────────────────────────────────────────────────────────────────────────
# Multi-project tests
# ───────────────────────────────────────────────────────────────────────────


class TestProjectInstance:
    """Tests for ProjectInstance class."""

    def test_project_instance_initial_state(self, config):
        """Test ProjectInstance initial state."""
        from texwatch.server import ProjectInstance
        proj = ProjectInstance(config, name="test")
        assert proj.name == "test"
        assert proj.compiling is False
        assert proj.last_result is None
        assert proj.synctex_data is None
        assert proj.viewer_state["page"] == 1
        assert proj.editor_state["file"] is None
        assert len(proj.websockets) == 0

    def test_project_instance_status_summary(self, config):
        """Test ProjectInstance status_summary."""
        from texwatch.server import ProjectInstance
        proj = ProjectInstance(config, name="thesis")
        summary = proj.status_summary()
        assert summary["name"] == "thesis"
        assert summary["main"] == "main.tex"
        assert summary["compiling"] is False
        assert summary["success"] is None  # not compiled yet

    def test_project_instance_status_after_compile(self, config):
        """Test status_summary after setting a compile result."""
        from texwatch.server import ProjectInstance
        proj = ProjectInstance(config, name="thesis")
        proj.last_result = CompileResult(
            success=True,
            errors=[],
            warnings=[],
        )
        summary = proj.status_summary()
        assert summary["success"] is True
        assert summary["error_count"] == 0


class TestMultiProjectServer:
    """Tests for multi-project server mode."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        """Create configs for two projects."""
        # Project A
        dir_a = tmp_path / "project_a"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n"
        )
        config_a = Config(
            main="main.tex",
            watch=["*.tex"],
            ignore=[],
            compiler="latexmk",
            port=0,
            config_path=dir_a / ".texwatch.yaml",
        )

        # Project B
        dir_b = tmp_path / "project_b"
        dir_b.mkdir()
        (dir_b / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nB\n\\end{document}\n"
        )
        config_b = Config(
            main="paper.tex",
            watch=["*.tex"],
            ignore=[],
            compiler="latexmk",
            port=0,
            config_path=dir_b / ".texwatch.yaml",
        )
        return [("alpha", config_a), ("beta", config_b)]

    @pytest.fixture
    def multi_server(self, multi_config):
        """Create a multi-project server."""
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        """Create an aiohttp test client for multi-project server."""
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_projects_endpoint(self, multi_client):
        """Test GET /projects returns all projects."""
        resp = await multi_client.get("/projects")
        assert resp.status == 200
        data = await resp.json()
        assert "projects" in data
        names = {p["name"] for p in data["projects"]}
        assert "alpha" in names
        assert "beta" in names

    @pytest.mark.asyncio
    async def test_root_serves_dashboard(self, multi_client):
        """Test GET / serves dashboard in multi-project mode."""
        resp = await multi_client.get("/")
        assert resp.status == 200
        content = await resp.text()
        assert "dashboard" in content.lower() or "texwatch" in content.lower()

    @pytest.mark.asyncio
    async def test_project_index(self, multi_client):
        """Test GET /p/{name}/ serves project viewer."""
        resp = await multi_client.get("/p/alpha/")
        assert resp.status == 200
        content = await resp.text()
        assert "TEXWATCH_BASE" in content
        assert "/p/alpha" in content

    @pytest.mark.asyncio
    async def test_project_status(self, multi_client, multi_server):
        """Test GET /p/{name}/status returns project status."""
        resp = await multi_client.get("/p/alpha/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["file"] == "main.tex"
        assert "compiling" in data

    @pytest.mark.asyncio
    async def test_project_not_found(self, multi_client):
        """Test /p/{name}/ with unknown name returns 404."""
        resp = await multi_client.get("/p/nonexistent/status")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_project_config(self, multi_client):
        """Test GET /p/{name}/config returns project config."""
        resp = await multi_client.get("/p/beta/config")
        assert resp.status == 200
        data = await resp.json()
        assert data["main"] == "paper.tex"

    @pytest.mark.asyncio
    async def test_project_files(self, multi_client):
        """Test GET /p/{name}/files returns file tree."""
        resp = await multi_client.get("/p/alpha/files")
        assert resp.status == 200
        data = await resp.json()
        names = [c["name"] for c in data["children"]]
        assert "main.tex" in names

    @pytest.mark.asyncio
    async def test_project_get_source(self, multi_client):
        """Test GET /p/{name}/source returns source."""
        resp = await multi_client.get("/p/alpha/source")
        assert resp.status == 200
        data = await resp.json()
        assert data["file"] == "main.tex"
        assert "\\documentclass" in data["content"]

    @pytest.mark.asyncio
    async def test_legacy_routes_on_multi_project(self, multi_client):
        """Test legacy /status on multi-project returns /projects data."""
        resp = await multi_client.get("/status")
        assert resp.status == 200
        data = await resp.json()
        # In multi-project, legacy /status returns the /projects response
        assert "projects" in data

    @pytest.mark.asyncio
    async def test_legacy_ws_on_multi_project(self, multi_client):
        """Test legacy /ws on multi-project returns 400."""
        with pytest.raises(Exception):
            # WebSocket connect should fail since legacy WS is rejected
            async with multi_client.ws_connect("/ws") as ws:
                pass

    @pytest.mark.asyncio
    async def test_project_websocket(self, multi_client, multi_server):
        """Test WebSocket at /p/{name}/ws."""
        async with multi_client.ws_connect("/p/alpha/ws") as ws:
            msg = await ws.receive_json()
            assert msg["type"] == "state"
            assert "compiling" in msg

    @pytest.mark.asyncio
    async def test_project_isolation(self, multi_client, multi_server):
        """Test that project state is isolated."""
        # Set state on alpha
        multi_server._projects["alpha"].last_result = CompileResult(
            success=True, errors=[], warnings=[],
        )

        # Alpha should show success
        resp_a = await multi_client.get("/p/alpha/status")
        data_a = await resp_a.json()
        assert data_a["success"] is True

        # Beta should not
        resp_b = await multi_client.get("/p/beta/status")
        data_b = await resp_b.json()
        assert data_b["success"] is None


class TestSingleProjectMode:
    """Tests for single-project mode (legacy compatibility)."""

    @pytest.mark.asyncio
    async def test_single_project_root_serves_index(self, client):
        """Test GET / in single-project mode serves index.html."""
        resp = await client.get("/")
        assert resp.status == 200
        content = await resp.text()
        assert "texwatch" in content

    def test_single_project_legacy_proxies(self, server):
        """Test legacy property proxies work for single-project."""
        assert server._compiling is False
        server._compiling = True
        assert server._compiling is True
        server._compiling = False  # reset

        assert server._viewer_state["page"] == 1
        server._viewer_state["page"] = 5
        assert server._viewer_state["page"] == 5

    def test_single_project_has_single(self, server):
        """Test single-project server has _single reference."""
        assert server._single is not None
        assert len(server._projects) == 1


class TestVisibleLinesComputed:
    """Tests for visible_lines computation on viewer_state update."""

    @pytest.mark.asyncio
    async def test_visible_lines_computed_on_viewer_state(self, client, server):
        """Test that visible_lines is computed when viewer_state is updated with synctex data."""
        from texwatch.synctex import PDFPosition, SourcePosition, SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={
                1: [
                    (400.0, SourcePosition(file="main.tex", line=5)),
                    (600.0, SourcePosition(file="main.tex", line=15)),
                    (800.0, SourcePosition(file="main.tex", line=25)),
                ],
            },
            source_to_pdf={},
            input_files={1: "main.tex"},
        )

        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state

            await ws.send_json({
                "type": "viewer_state",
                "state": {"page": 1, "total_pages": 5},
            })

            import asyncio
            await asyncio.sleep(0.1)

            assert server._viewer_state["visible_lines"] == (5, 25)

    @pytest.mark.asyncio
    async def test_visible_lines_none_without_synctex(self, client, server):
        """Test that visible_lines stays None when no synctex data."""
        server._synctex_data = None

        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()

            await ws.send_json({
                "type": "viewer_state",
                "state": {"page": 1, "total_pages": 5},
            })

            import asyncio
            await asyncio.sleep(0.1)

            assert server._viewer_state.get("visible_lines") is None


class TestGotoUsesEditorFile:
    """Tests for multi-file forward sync in goto handler."""

    @pytest.mark.asyncio
    async def test_goto_uses_editor_file(self, client, server):
        """Test that goto uses editor_state file when no file in request."""
        from texwatch.synctex import PDFPosition, SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("chapters/intro.tex", 10): [
                    PDFPosition(page=3, x=72.0, y=500.0, width=200.0, height=12.0)
                ],
            },
            input_files={1: "chapters/intro.tex"},
        )
        server._editor_state = {"file": "chapters/intro.tex", "line": 10}

        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()

            resp = await client.post("/goto", json={"line": 10})
            assert resp.status == 200
            data = await resp.json()
            assert data["success"] is True
            assert data["page"] == 3

            msg = await ws.receive_json()
            assert msg["type"] == "goto"
            assert msg["page"] == 3

    @pytest.mark.asyncio
    async def test_goto_explicit_file_overrides_editor(self, client, server):
        """Test that explicit file in request takes priority over editor_state."""
        from texwatch.synctex import PDFPosition, SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("appendix.tex", 5): [
                    PDFPosition(page=7, x=72.0, y=300.0, width=100.0, height=10.0)
                ],
            },
            input_files={1: "appendix.tex"},
        )
        server._editor_state = {"file": "main.tex", "line": 1}

        resp = await client.post("/goto", json={"line": 5, "file": "appendix.tex"})
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["page"] == 7

    @pytest.mark.asyncio
    async def test_goto_falls_back_to_main_file(self, client, server):
        """Test goto falls back to main file when no editor state or explicit file."""
        from texwatch.synctex import PDFPosition, SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 3): [
                    PDFPosition(page=1, x=72.0, y=200.0, width=100.0, height=10.0)
                ],
            },
            input_files={1: "main.tex"},
        )
        server._editor_state = {"file": None, "line": None}

        resp = await client.post("/goto", json={"line": 3})
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["page"] == 1


class TestErrorsEndpoint:
    """Tests for GET /errors endpoint."""

    @pytest.mark.asyncio
    async def test_errors_no_compile_result(self, client, server):
        """Test GET /errors when no compilation has happened yet."""
        resp = await client.get("/errors")
        assert resp.status == 200

        data = await resp.json()
        assert data["errors"] == []
        assert data["warnings"] == []

    @pytest.mark.asyncio
    async def test_errors_with_errors_and_warnings(self, client, server):
        """Test GET /errors returns errors and warnings from last compile."""
        server._last_result = CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file="main.tex", line=42, message="Undefined control sequence",
                    type="error",
                    context=["line 37", "line 38", "line 39", "line 40", "line 41",
                             ">>> line 42 <<<", "line 43", "line 44", "line 45",
                             "line 46", "line 47"],
                ),
            ],
            warnings=[
                CompileMessage(
                    file="main.tex", line=10, message="Underfull hbox",
                    type="warning",
                ),
            ],
        )

        resp = await client.get("/errors")
        assert resp.status == 200

        data = await resp.json()
        assert len(data["errors"]) == 1
        assert len(data["warnings"]) == 1

        err = data["errors"][0]
        assert err["file"] == "main.tex"
        assert err["line"] == 42
        assert err["message"] == "Undefined control sequence"
        assert "context" in err
        assert len(err["context"]) == 11
        assert ">>> line 42 <<<" in err["context"]

        warn = data["warnings"][0]
        assert warn["file"] == "main.tex"
        assert warn["line"] == 10
        # Warning without context should not have context key
        assert "context" not in warn

    @pytest.mark.asyncio
    async def test_errors_with_successful_compile(self, client, server):
        """Test GET /errors with successful compile returns empty lists."""
        server._last_result = CompileResult(
            success=True,
            errors=[],
            warnings=[],
        )

        resp = await client.get("/errors")
        assert resp.status == 200

        data = await resp.json()
        assert data["errors"] == []
        assert data["warnings"] == []

    @pytest.mark.asyncio
    async def test_errors_context_not_present_when_none(self, client, server):
        """Test that context key is omitted when context is None."""
        server._last_result = CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file="main.tex", line=5, message="Missing $",
                    type="error",
                    # context is None by default
                ),
            ],
        )

        resp = await client.get("/errors")
        data = await resp.json()
        assert "context" not in data["errors"][0]


class TestErrorsEndpointMultiProject:
    """Tests for GET /p/{name}/errors endpoint in multi-project mode."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        """Create configs for two projects."""
        dir_a = tmp_path / "project_a"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n"
        )
        config_a = Config(
            main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
            port=0, config_path=dir_a / ".texwatch.yaml",
        )
        return [("alpha", config_a)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_project_errors_endpoint(self, multi_client, multi_server):
        """Test GET /p/{name}/errors returns project errors."""
        multi_server._projects["alpha"].last_result = CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file="main.tex", line=3, message="Bad command",
                    type="error",
                    context=["line 1", "line 2", ">>> line 3 <<<"],
                ),
            ],
        )

        resp = await multi_client.get("/p/alpha/errors")
        assert resp.status == 200

        data = await resp.json()
        assert len(data["errors"]) == 1
        assert data["errors"][0]["context"] == ["line 1", "line 2", ">>> line 3 <<<"]

    @pytest.mark.asyncio
    async def test_project_errors_not_found(self, multi_client):
        """Test GET /p/{name}/errors with unknown project returns 404."""
        resp = await multi_client.get("/p/nonexistent/errors")
        assert resp.status == 404


# ───────────────────────────────────────────────────────────────────────────
# Context endpoint tests
# ───────────────────────────────────────────────────────────────────────────


class TestContextEndpoint:
    """Tests for GET /context endpoint."""

    @pytest.mark.asyncio
    async def test_context_no_compile_result(self, client, server):
        """Test /context returns default context when no compilation has happened."""
        from texwatch.structure import DocumentStructure

        with patch("texwatch.server.parse_structure", return_value=DocumentStructure()):
            resp = await client.get("/context")
            assert resp.status == 200

            data = await resp.json()
            assert data["compiling"] is False
            assert data["errors_count"] == 0
            assert data["warnings_count"] == 0
            assert data["current_section"] is None
            assert data["editor"]["file"] is None
            assert data["editor"]["line"] is None
            assert data["viewer"]["page"] == 1

    @pytest.mark.asyncio
    async def test_context_with_compile_result(self, client, server):
        """Test /context returns full context after compilation."""
        from texwatch.structure import DocumentStructure

        server._last_result = CompileResult(
            success=False,
            errors=[
                CompileMessage(file="main.tex", line=10, message="err1", type="error"),
                CompileMessage(file="main.tex", line=20, message="err2", type="error"),
            ],
            warnings=[
                CompileMessage(file="main.tex", line=5, message="warn", type="warning"),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=DocumentStructure()):
            resp = await client.get("/context")
            assert resp.status == 200

            data = await resp.json()
            assert data["compiling"] is False
            assert data["errors_count"] == 2
            assert data["warnings_count"] == 1

    @pytest.mark.asyncio
    async def test_context_current_section(self, client, server):
        """Test current_section is determined from editor state."""
        from texwatch.structure import DocumentStructure, Section

        server._editor_state = {"file": "main.tex", "line": 42}

        structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
                Section(level="section", title="Methods", file="main.tex", line=30),
                Section(level="section", title="Results", file="main.tex", line=50),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=structure):
            resp = await client.get("/context")
            assert resp.status == 200

            data = await resp.json()
            # Line 42 is after "Methods" (line 30) but before "Results" (line 50)
            assert data["current_section"] == "Methods"

    @pytest.mark.asyncio
    async def test_context_includes_page_limit(self, client, server):
        """Test /context includes page_limit from config."""
        from texwatch.structure import DocumentStructure

        server._single.config.page_limit = 8

        with patch("texwatch.server.parse_structure", return_value=DocumentStructure()):
            resp = await client.get("/context")
            assert resp.status == 200

            data = await resp.json()
            assert data["page_limit"] == 8

    @pytest.mark.asyncio
    async def test_context_includes_word_count(self, client, server):
        """Test /context includes word_count from parse_structure."""
        from texwatch.structure import DocumentStructure

        structure = DocumentStructure(word_count=4200)

        with patch("texwatch.server.parse_structure", return_value=structure):
            resp = await client.get("/context")
            assert resp.status == 200

            data = await resp.json()
            assert data["word_count"] == 4200


class TestContextEndpointMultiProject:
    """Tests for GET /p/{name}/context endpoint in multi-project mode."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        """Create configs for two projects."""
        dir_a = tmp_path / "project_a"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n"
        )
        config_a = Config(
            main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
            port=0, config_path=dir_a / ".texwatch.yaml",
        )
        return [("alpha", config_a)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_project_context_endpoint(self, multi_client, multi_server):
        """Test GET /p/{name}/context returns project context."""
        from texwatch.structure import DocumentStructure

        with patch("texwatch.server.parse_structure", return_value=DocumentStructure()):
            resp = await multi_client.get("/p/alpha/context")
            assert resp.status == 200

            data = await resp.json()
            assert "editor" in data
            assert "viewer" in data
            assert "compiling" in data
            assert "errors_count" in data
            assert "warnings_count" in data
            assert "current_section" in data
            assert "page_limit" in data
            assert "word_count" in data

    @pytest.mark.asyncio
    async def test_project_context_not_found(self, multi_client):
        """Test GET /p/{name}/context with unknown project returns 404."""
        resp = await multi_client.get("/p/nonexistent/context")
        assert resp.status == 404


# ───────────────────────────────────────────────────────────────────────────
# Structure endpoint tests
# ───────────────────────────────────────────────────────────────────────────


class TestStructureEndpoint:
    """Tests for GET /structure endpoint."""

    @pytest.mark.asyncio
    async def test_structure_returns_sections_todos_inputs(self, client, server):
        """Test /structure returns parsed structure with sections, todos, inputs."""
        from texwatch.structure import (
            DocumentStructure,
            InputFile,
            Section,
            TodoItem,
        )

        structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=15),
            ],
            todos=[
                TodoItem(text="Add more references", file="related.tex", line=12, tag="TODO"),
            ],
            inputs=[
                InputFile(path="chapters/intro.tex", file="main.tex", line=5),
            ],
            word_count=4200,
        )

        with patch("texwatch.server.parse_structure", return_value=structure):
            resp = await client.get("/structure")
            assert resp.status == 200

            data = await resp.json()
            assert len(data["sections"]) == 1
            assert data["sections"][0]["level"] == "section"
            assert data["sections"][0]["title"] == "Introduction"
            assert data["sections"][0]["file"] == "main.tex"
            assert data["sections"][0]["line"] == 15

            assert len(data["todos"]) == 1
            assert data["todos"][0]["text"] == "Add more references"
            assert data["todos"][0]["file"] == "related.tex"
            assert data["todos"][0]["line"] == 12
            assert data["todos"][0]["tag"] == "TODO"

            assert len(data["inputs"]) == 1
            assert data["inputs"][0]["path"] == "chapters/intro.tex"
            assert data["inputs"][0]["file"] == "main.tex"
            assert data["inputs"][0]["line"] == 5

            assert data["word_count"] == 4200

    @pytest.mark.asyncio
    async def test_structure_empty_project(self, client, server):
        """Test /structure returns empty lists for empty project."""
        from texwatch.structure import DocumentStructure

        structure = DocumentStructure()

        with patch("texwatch.server.parse_structure", return_value=structure):
            resp = await client.get("/structure")
            assert resp.status == 200

            data = await resp.json()
            assert data["sections"] == []
            assert data["todos"] == []
            assert data["inputs"] == []
            assert data["word_count"] is None


class TestStructureEndpointMultiProject:
    """Tests for GET /p/{name}/structure endpoint in multi-project mode."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        """Create configs for two projects."""
        dir_a = tmp_path / "project_a"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n"
        )
        config_a = Config(
            main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
            port=0, config_path=dir_a / ".texwatch.yaml",
        )
        return [("alpha", config_a)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_project_structure_endpoint(self, multi_client, multi_server):
        """Test GET /p/{name}/structure returns project structure."""
        from texwatch.structure import DocumentStructure, Section

        structure = DocumentStructure(
            sections=[
                Section(level="section", title="Intro", file="main.tex", line=5),
            ],
            word_count=1000,
        )

        with patch("texwatch.server.parse_structure", return_value=structure):
            resp = await multi_client.get("/p/alpha/structure")
            assert resp.status == 200

            data = await resp.json()
            assert len(data["sections"]) == 1
            assert data["sections"][0]["title"] == "Intro"
            assert data["word_count"] == 1000

    @pytest.mark.asyncio
    async def test_project_structure_not_found(self, multi_client):
        """Test GET /p/{name}/structure with unknown project returns 404."""
        resp = await multi_client.get("/p/nonexistent/structure")
        assert resp.status == 404


class TestGotoSection:
    """Tests for section-based goto navigation."""

    @pytest.mark.asyncio
    async def test_goto_section_exact_match(self, client, server):
        """Test goto section with exact title match and SyncTeX hit."""
        from texwatch.synctex import PDFPosition, SyncTeXData
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [
                    PDFPosition(page=2, x=72.0, y=500.0, width=200.0, height=14.0)
                ],
            },
            input_files={},
        )

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
                Section(level="section", title="Conclusion", file="main.tex", line=50),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Introduction"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["page"] == 2
        assert data["section"] == "Introduction"

    @pytest.mark.asyncio
    async def test_goto_section_substring_match(self, client, server):
        """Test goto section with substring match (e.g. 'intro' matches 'Introduction')."""
        from texwatch.synctex import PDFPosition, SyncTeXData
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [
                    PDFPosition(page=2, x=72.0, y=500.0, width=200.0, height=14.0)
                ],
            },
            input_files={},
        )

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
                Section(level="section", title="Conclusion", file="main.tex", line=50),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "intro"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["section"] == "Introduction"

    @pytest.mark.asyncio
    async def test_goto_section_case_insensitive(self, client, server):
        """Test goto section with case-insensitive match."""
        from texwatch.synctex import PDFPosition, SyncTeXData
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [
                    PDFPosition(page=2, x=72.0, y=500.0, width=200.0, height=14.0)
                ],
            },
            input_files={},
        )

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "INTRODUCTION"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["section"] == "Introduction"

    @pytest.mark.asyncio
    async def test_goto_section_not_found(self, client, server):
        """Test goto section returns 404 when no section matches."""
        from texwatch.structure import Section, DocumentStructure

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
                Section(level="section", title="Conclusion", file="main.tex", line=50),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Nonexistent"})

        assert resp.status == 404
        data = await resp.json()
        assert "No section matching" in data["error"]
        assert "available_sections" in data
        assert "Introduction" in data["available_sections"]
        assert "Conclusion" in data["available_sections"]

    @pytest.mark.asyncio
    async def test_goto_section_no_synctex(self, client, server):
        """Test goto section falls back to page estimation when no SyncTeX data."""
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = None
        server._viewer_state["total_pages"] = 10

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=2),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Introduction"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True
        assert data["section"] == "Introduction"
        # main.tex has 4 lines, section at line 2, 10 pages
        # estimated_page = round(2/4 * 10) = round(5.0) = 5
        assert data["page"] == 5

    @pytest.mark.asyncio
    async def test_goto_section_prefers_exact_match(self, client, server):
        """Test goto section prefers exact match when multiple sections match substring."""
        from texwatch.synctex import PDFPosition, SyncTeXData
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 30): [
                    PDFPosition(page=4, x=72.0, y=400.0, width=200.0, height=14.0)
                ],
                ("main.tex", 10): [
                    PDFPosition(page=2, x=72.0, y=500.0, width=200.0, height=14.0)
                ],
            },
            input_files={},
        )

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Introduction to Methods", file="main.tex", line=10),
                Section(level="section", title="Introduction", file="main.tex", line=30),
                Section(level="section", title="Conclusion", file="main.tex", line=50),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Introduction"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        # Should prefer exact match "Introduction" (line 30, page 4)
        # over substring match "Introduction to Methods" (line 10, page 2)
        assert data["section"] == "Introduction"
        assert data["page"] == 4

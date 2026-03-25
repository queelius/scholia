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


# ───────────────────────────────────────────────────────────────────────────
# Phase 2 coverage tests
# ───────────────────────────────────────────────────────────────────────────


class TestMultiProjectLegacyBehavior:
    """Tests for legacy routes on a multi-project server.

    Aggregate endpoints (files, errors, structure, context, compile) return 200.
    Single-project-only endpoints (goto, capture, source, pdf, config) return
    structured JSON 400 with project list.
    """

    @pytest.fixture
    def multi_config(self, tmp_path):
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nB\n\\end{document}\n")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")
        return [("a", config_a), ("b", config_b)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    # --- Structured 400 endpoints (require --project) ---

    @pytest.mark.asyncio
    async def test_legacy_goto_json_error(self, multi_client):
        """Test /goto returns JSON 400 with project list."""
        resp = await multi_client.post("/goto", json={"line": 1})
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data
        assert "projects" in data
        assert set(data["projects"]) == {"a", "b"}
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_legacy_capture_json_error(self, multi_client):
        """Test /capture returns JSON 400 with project list."""
        resp = await multi_client.get("/capture")
        assert resp.status == 400
        data = await resp.json()
        assert "projects" in data
        assert set(data["projects"]) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_legacy_source_json_error(self, multi_client):
        """Test /source GET returns JSON 400 with project list."""
        resp = await multi_client.get("/source")
        assert resp.status == 400
        data = await resp.json()
        assert "projects" in data
        assert set(data["projects"]) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_legacy_post_source_json_error(self, multi_client):
        """Test /source POST returns JSON 400 with project list."""
        resp = await multi_client.post("/source", json={"file": "x", "content": "y"})
        assert resp.status == 400
        data = await resp.json()
        assert "projects" in data

    @pytest.mark.asyncio
    async def test_legacy_pdf_json_error(self, multi_client):
        """Test /pdf returns JSON 400 with project list."""
        resp = await multi_client.get("/pdf")
        assert resp.status == 400
        data = await resp.json()
        assert "projects" in data

    @pytest.mark.asyncio
    async def test_legacy_config_json_error(self, multi_client):
        """Test /config returns JSON 400 with project list."""
        resp = await multi_client.get("/config")
        assert resp.status == 400
        data = await resp.json()
        assert "projects" in data

    # --- Aggregate endpoints (return combined data) ---

    @pytest.mark.asyncio
    async def test_legacy_files_aggregate(self, multi_client):
        """Test /files returns combined file trees keyed by project name."""
        resp = await multi_client.get("/files")
        assert resp.status == 200
        data = await resp.json()
        assert "a" in data
        assert "b" in data
        # Each project's files response should have root/children
        assert "root" in data["a"]
        assert "children" in data["a"]

    @pytest.mark.asyncio
    async def test_legacy_errors_aggregate(self, multi_client):
        """Test /errors returns combined errors keyed by project name."""
        resp = await multi_client.get("/errors")
        assert resp.status == 200
        data = await resp.json()
        assert "a" in data
        assert "b" in data
        # Each should have errors/warnings keys
        assert "errors" in data["a"]
        assert "warnings" in data["a"]

    @pytest.mark.asyncio
    async def test_legacy_structure_aggregate(self, multi_client):
        """Test /structure returns combined structure keyed by project name."""
        resp = await multi_client.get("/structure")
        assert resp.status == 200
        data = await resp.json()
        assert "a" in data
        assert "b" in data
        assert "sections" in data["a"]

    @pytest.mark.asyncio
    async def test_legacy_context_aggregate(self, multi_client):
        """Test /context returns combined context keyed by project name."""
        resp = await multi_client.get("/context")
        assert resp.status == 200
        data = await resp.json()
        assert "a" in data
        assert "b" in data
        assert "editor" in data["a"]
        assert "viewer" in data["a"]

    @pytest.mark.asyncio
    async def test_legacy_compile_aggregate(self, multi_client, multi_server):
        """Test /compile compiles all projects and returns combined results."""
        with patch("texwatch.server.compile_tex") as mock_compile:
            mock_compile.return_value = CompileResult(success=True)
            resp = await multi_client.post("/compile")
        assert resp.status == 200
        data = await resp.json()
        assert "projects" in data
        assert "a" in data["projects"]
        assert "b" in data["projects"]
        assert data["projects"]["a"]["success"] is True
        assert data["projects"]["b"]["success"] is True


class TestBroadcastErrorHandling:
    """Tests for WebSocket broadcast error handling."""

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_ws(self, server):
        """Test that broadcast discards WebSocket connections that fail on send."""
        from texwatch.server import ProjectInstance

        proj = server._single
        ws_good = MagicMock(spec=web.WebSocketResponse)
        ws_good.send_json = AsyncMock()
        ws_bad = MagicMock(spec=web.WebSocketResponse)
        ws_bad.send_json = AsyncMock(side_effect=ConnectionError("closed"))

        proj.websockets = {ws_good, ws_bad}
        await proj.broadcast({"type": "test"})

        ws_good.send_json.assert_called_once()
        ws_bad.send_json.assert_called_once()
        assert ws_bad not in proj.websockets
        assert ws_good in proj.websockets


class TestSymlinkSecurity:
    """Tests for symlink security checks in source and files endpoints."""

    @pytest.mark.asyncio
    async def test_get_source_symlink_blocked(self, client, config):
        """Test GET /source blocks symlinks."""
        watch_dir = config.config_path.parent
        link = watch_dir / "evil.tex"
        link.symlink_to("/etc/hostname")

        resp = await client.get("/source?file=evil.tex")
        assert resp.status == 403
        data = await resp.json()
        assert "symlink" in data["error"].lower() or "denied" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_post_source_symlink_blocked(self, client, config):
        """Test POST /source blocks symlinks."""
        watch_dir = config.config_path.parent
        link = watch_dir / "evil.tex"
        link.symlink_to("/etc/hostname")

        resp = await client.post("/source", json={"file": "evil.tex", "content": "hack"})
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_files_symlink_outside_excluded(self, client, config):
        """Test /files excludes symlinks pointing outside the project."""
        watch_dir = config.config_path.parent
        sub = watch_dir / "linked_dir"
        sub.symlink_to("/tmp")

        resp = await client.get("/files")
        data = await resp.json()
        names = [c["name"] for c in data["children"]]
        assert "linked_dir" not in names


class TestGotoEdgeCases:
    """Tests for goto handler edge cases."""

    @pytest.mark.asyncio
    async def test_goto_line_non_tex_file(self, config, tmp_path):
        """Test goto line on non-tex file returns 501."""
        # Create a markdown-based config
        md_config = Config(
            main="paper.md",
            watch=["*.md"],
            ignore=[],
            compiler="latexmk",
            port=0,
            config_path=tmp_path / ".texwatch.yaml",
        )
        (tmp_path / "paper.md").write_text("# Hello\n\nWorld\n")
        server = TexWatchServer(md_config)

        async with TestClient(TestServer(server.app)) as c:
            resp = await c.post("/goto", json={"line": 1})
            assert resp.status == 501
            data = await resp.json()
            assert "SyncTeX" in data["error"]

    @pytest.mark.asyncio
    async def test_goto_section_parse_failure(self, client, server):
        """Test goto section when parse_structure raises returns 500."""
        with patch("texwatch.server.parse_structure", side_effect=RuntimeError("parse error")):
            resp = await client.post("/goto", json={"section": "Intro"})
        assert resp.status == 500
        data = await resp.json()
        assert "Failed to parse" in data["error"]

    @pytest.mark.asyncio
    async def test_goto_section_synctex_miss_with_estimation(self, client, server):
        """Test goto section falls back to page estimation when synctex misses."""
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = None
        server._viewer_state["total_pages"] = 10

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Results", file="main.tex", line=3),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Results"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True
        assert data["section"] == "Results"
        assert "page" in data

    @pytest.mark.asyncio
    async def test_goto_section_no_pages(self, client, server):
        """Test goto section with no total_pages falls to page 1 fallback."""
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = None
        server._viewer_state["total_pages"] = 0

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Discussion", file="main.tex", line=3),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Discussion"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True
        assert data["section"] == "Discussion"


class TestStructureContextFailures:
    """Tests for parse_structure failure handling in structure and context endpoints."""

    @pytest.mark.asyncio
    async def test_structure_parse_failure(self, client):
        """Test /structure returns empty structure on parse error."""
        with patch("texwatch.server.parse_structure", side_effect=RuntimeError("bad")):
            resp = await client.get("/structure")
        assert resp.status == 200
        data = await resp.json()
        assert data["sections"] == []
        assert data["todos"] == []
        assert data["inputs"] == []
        assert data["word_count"] is None

    @pytest.mark.asyncio
    async def test_context_parse_failure(self, client):
        """Test /context returns valid context with null section on parse error."""
        with patch("texwatch.server.parse_structure", side_effect=RuntimeError("bad")):
            resp = await client.get("/context")
        assert resp.status == 200
        data = await resp.json()
        assert data["current_section"] is None
        assert data["word_count"] is None


class TestDashboardFallback:
    """Tests for dashboard fallback when dashboard.html is missing."""

    @pytest.mark.asyncio
    async def test_dashboard_fallback_no_file(self, tmp_path):
        """Test that dashboard serves inline HTML when dashboard.html is missing."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nB\n\\end{document}\n")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")

        server = TexWatchServer(projects=[("alpha", config_a), ("beta", config_b)])

        # Rename the real dashboard.html temporarily so the fallback path runs
        static_dir = Path(__file__).parent.parent / "texwatch" / "static"
        dashboard = static_dir / "dashboard.html"
        backup = static_dir / "dashboard.html.bak"
        renamed = False
        if dashboard.exists():
            dashboard.rename(backup)
            renamed = True
        try:
            async with TestClient(TestServer(server.app)) as c:
                resp = await c.get("/")
                assert resp.status == 200
                content = await resp.text()
                assert "dashboard" in content.lower()
                assert "alpha" in content
                assert "beta" in content
        finally:
            if renamed:
                backup.rename(dashboard)

    @pytest.mark.asyncio
    async def test_dashboard_html_escapes_project_names(self, tmp_path):
        """Test that project names are HTML-escaped in the dashboard fallback."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("doc\n")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("doc\n")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")

        # Use a project name containing HTML-special characters (needs multi-project for dashboard)
        server = TexWatchServer(projects=[
            ("<script>alert(1)</script>", config_a),
            ("safe", config_b),
        ])

        static_dir = Path(__file__).parent.parent / "texwatch" / "static"
        dashboard = static_dir / "dashboard.html"
        backup = static_dir / "dashboard.html.bak"
        renamed = False
        if dashboard.exists():
            dashboard.rename(backup)
            renamed = True
        try:
            async with TestClient(TestServer(server.app)) as c:
                resp = await c.get("/")
                assert resp.status == 200
                content = await resp.text()
                # The raw <script> tag must NOT appear — it must be escaped
                assert "<script>alert(1)</script>" not in content
                assert "&lt;script&gt;" in content
        finally:
            if renamed:
                backup.rename(dashboard)


class TestMultiProjectPropertyProxies:
    """Tests for multi-project property proxies."""

    def test_multi_compiling_any(self, tmp_path):
        """Test _compiling returns True if any project is compiling."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("hi")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("hi")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")

        server = TexWatchServer(projects=[("a", config_a), ("b", config_b)])
        assert server._compiling is False

        server._projects["a"].compiling = True
        assert server._compiling is True

    def test_multi_websockets_union(self, tmp_path):
        """Test _websockets returns union of all project WebSocket sets."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("hi")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("hi")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")

        server = TexWatchServer(projects=[("a", config_a), ("b", config_b)])
        ws1 = MagicMock()
        ws2 = MagicMock()
        server._projects["a"].websockets.add(ws1)
        server._projects["b"].websockets.add(ws2)

        assert ws1 in server._websockets
        assert ws2 in server._websockets
        assert len(server._websockets) == 2


class TestFilesSymlinkFile:
    """Tests for file-level symlink exclusion in _build_file_tree."""

    @pytest.mark.asyncio
    async def test_files_file_symlink_outside_excluded(self, client, config):
        """Test /files excludes file symlinks pointing outside the project."""
        watch_dir = config.config_path.parent
        link = watch_dir / "external.tex"
        link.symlink_to("/etc/hostname")

        resp = await client.get("/files")
        data = await resp.json()
        names = [c["name"] for c in data["children"]]
        assert "external.tex" not in names


class TestCaptureViewerPage:
    """Tests for capture using viewer state page."""

    @pytest.mark.asyncio
    async def test_capture_uses_viewer_page(self, client, server, config):
        """Test capture without ?page= uses viewer_state page."""
        import pymupdf

        pdf_path = config.config_path.parent / "main.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.new_page()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        server._viewer_state["page"] = 2

        resp = await client.get("/capture")
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "image/png"


class TestGotoLineNoTotalLines:
    """Tests for goto line fallback when total_lines is 0."""

    @pytest.mark.asyncio
    async def test_goto_line_no_total_lines(self, client, server, config):
        """Test goto line estimation when source file can't be read."""
        server._viewer_state["total_pages"] = 5

        # Replace main file with empty so total_lines = 0
        main_path = config.config_path.parent / "main.tex"
        main_path.write_text("")

        resp = await client.post("/goto", json={"line": 3})
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True
        # When total_lines=0, estimated_page = max(1, min(line, total_pages))
        assert data["page"] == 3


class TestGotoSectionNoTotalLines:
    """Tests for goto section estimation when total_lines is 0."""

    @pytest.mark.asyncio
    async def test_goto_section_no_total_lines(self, client, server, config):
        """Test goto section estimation when source file can't be read."""
        from texwatch.structure import Section, DocumentStructure

        server._synctex_data = None
        server._viewer_state["total_pages"] = 5

        # Empty main so total_lines = 0
        main_path = config.config_path.parent / "main.tex"
        main_path.write_text("")

        mock_structure = DocumentStructure(
            sections=[
                Section(level="section", title="Intro", file="main.tex", line=3),
            ],
        )

        with patch("texwatch.server.parse_structure", return_value=mock_structure):
            resp = await client.post("/goto", json={"section": "Intro"})

        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["estimated"] is True
        assert data["page"] == 3


class TestParallelCompilation:
    """Test that multi-project start compiles all projects in parallel."""

    @pytest.mark.asyncio
    async def test_start_compiles_parallel(self, tmp_path):
        """Verify start() uses asyncio.gather to compile all projects."""
        # Create two project directories
        for name in ("proj1", "proj2"):
            d = tmp_path / name
            d.mkdir()
            (d / "main.tex").write_text("\\documentclass{article}\\begin{document}Hi\\end{document}\n")

        projects = [
            ("proj1", Config(
                main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                port=0, config_path=tmp_path / "proj1" / ".texwatch.yaml",
            )),
            ("proj2", Config(
                main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                port=0, config_path=tmp_path / "proj2" / ".texwatch.yaml",
            )),
        ]

        server = TexWatchServer(projects=projects)

        compile_calls = []
        original_do_compile = type(list(server._projects.values())[0]).do_compile

        async def mock_do_compile(self_proj):
            compile_calls.append(self_proj.name)

        # Patch do_compile on each project instance
        for proj in server._projects.values():
            proj.do_compile = lambda p=proj: mock_do_compile(p)

        await server.start()

        # Both projects should have been compiled
        assert set(compile_calls) == {"proj1", "proj2"}

        # Clean up
        await server.stop()


# ---------------------------------------------------------------------------
# Test: Current Project Tracking
# ---------------------------------------------------------------------------


class TestCurrentProjectTracking:
    """Tests for current-project pointer feature."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        """Create configs for two projects."""
        dir_a = tmp_path / "project_a"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n"
        )
        config_a = Config(
            main="main.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=dir_a / ".texwatch.yaml",
        )

        dir_b = tmp_path / "project_b"
        dir_b.mkdir()
        (dir_b / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nB\n\\end{document}\n"
        )
        config_b = Config(
            main="paper.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=dir_b / ".texwatch.yaml",
        )
        return [("alpha", config_a), ("beta", config_b)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_initial_current_is_none(self, multi_server):
        """Test that _current_project_name starts as None."""
        assert multi_server._current_project_name is None

    @pytest.mark.asyncio
    async def test_project_access_sets_current(self, multi_client, multi_server):
        """Test accessing /p/alpha/status sets current to alpha."""
        resp = await multi_client.get("/p/alpha/status")
        assert resp.status == 200
        assert multi_server._current_project_name == "alpha"

    @pytest.mark.asyncio
    async def test_current_switches_on_different_project(self, multi_client, multi_server):
        """Test accessing different projects updates current."""
        await multi_client.get("/p/alpha/status")
        assert multi_server._current_project_name == "alpha"
        await multi_client.get("/p/beta/status")
        assert multi_server._current_project_name == "beta"

    @pytest.mark.asyncio
    async def test_unprefixed_goto_works_after_project_access(self, multi_client, multi_server):
        """Test unprefixed goto auto-selects after accessing a project."""
        await multi_client.get("/p/alpha/status")
        resp = await multi_client.post("/goto", json={"page": 1})
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_unprefixed_goto_400_when_no_current(self, multi_client, multi_server):
        """Test unprefixed goto still returns 400 when no current set."""
        resp = await multi_client.post("/goto", json={"page": 1})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_get_current_returns_name(self, multi_client, multi_server):
        """Test GET /current returns current project name."""
        multi_server._current_project_name = "alpha"
        resp = await multi_client.get("/current")
        assert resp.status == 200
        data = await resp.json()
        assert data["current"] == "alpha"

    @pytest.mark.asyncio
    async def test_get_current_returns_null(self, multi_client, multi_server):
        """Test GET /current returns null when no current."""
        resp = await multi_client.get("/current")
        assert resp.status == 200
        data = await resp.json()
        assert data["current"] is None
        assert "projects" in data

    @pytest.mark.asyncio
    async def test_post_current_switches_project(self, multi_client, multi_server):
        """Test POST /current switches the current project."""
        resp = await multi_client.post("/current", json={"project": "beta"})
        assert resp.status == 200
        data = await resp.json()
        assert data["current"] == "beta"
        assert multi_server._current_project_name == "beta"

    @pytest.mark.asyncio
    async def test_post_current_unknown_project(self, multi_client, multi_server):
        """Test POST /current with unknown project returns 400."""
        resp = await multi_client.post("/current", json={"project": "unknown"})
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_auto_selected_header(self, multi_client, multi_server):
        """Test X-Texwatch-Project header on auto-selected responses."""
        await multi_client.get("/p/alpha/status")
        resp = await multi_client.post("/goto", json={"page": 1})
        assert resp.status == 200
        assert resp.headers.get("X-Texwatch-Project") == "alpha"


# ---------------------------------------------------------------------------
# Test: Event Logging
# ---------------------------------------------------------------------------


class TestEventLogging:
    """Tests for per-project and global event logging."""

    @pytest.fixture
    def config(self, tmp_path):
        main_file = tmp_path / "main.tex"
        main_file.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
        )
        return Config(
            main="main.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=tmp_path / ".texwatch.yaml",
        )

    @pytest.fixture
    def server(self, config):
        return TexWatchServer(config)

    @pytest.fixture
    async def client(self, server):
        async with TestClient(TestServer(server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_empty_activity_initially(self, client):
        """Test /activity returns empty events initially."""
        resp = await client.get("/activity")
        assert resp.status == 200
        data = await resp.json()
        assert data["events"] == []

    @pytest.mark.asyncio
    async def test_goto_logs_event(self, client, server):
        """Test goto logs an event."""
        await client.post("/goto", json={"page": 3})
        resp = await client.get("/activity")
        data = await resp.json()
        events = data["events"]
        goto_events = [e for e in events if e["type"] == "goto"]
        assert len(goto_events) == 1
        assert goto_events[0]["target_type"] == "page"
        assert goto_events[0]["value"] == 3

    @pytest.mark.asyncio
    async def test_activity_limit_filter(self, client, server):
        """Test /activity with limit parameter."""
        for i in range(5):
            await client.post("/goto", json={"page": i + 1})
        resp = await client.get("/activity?limit=2")
        data = await resp.json()
        assert len(data["events"]) == 2

    @pytest.mark.asyncio
    async def test_activity_type_filter(self, client, server):
        """Test /activity with type filter."""
        await client.post("/goto", json={"page": 1})
        resp = await client.get("/activity?type=goto")
        data = await resp.json()
        assert len(data["events"]) >= 1
        assert all(e["type"] == "goto" for e in data["events"])

    @pytest.mark.asyncio
    async def test_activity_newest_first(self, client, server):
        """Test /activity returns events newest first."""
        await client.post("/goto", json={"page": 1})
        await client.post("/goto", json={"page": 2})
        resp = await client.get("/activity")
        data = await resp.json()
        events = [e for e in data["events"] if e["type"] == "goto"]
        assert events[0]["value"] == 2
        assert events[1]["value"] == 1

    @pytest.mark.asyncio
    async def test_page_view_dedup(self, server):
        """Test page_view events are deduplicated."""
        proj = server._single
        # Simulate same page twice
        proj._last_page_view = {}
        proj.viewer_state["page"] = 5
        proj.viewer_state["total_pages"] = 10

        ws = MagicMock()
        ws.send_json = AsyncMock()

        await server._handle_ws_message(ws, {"type": "viewer_state", "state": {"page": 5}}, proj)
        count1 = len([e for e in proj.events if e["type"] == "page_view"])
        await server._handle_ws_message(ws, {"type": "viewer_state", "state": {"page": 5}}, proj)
        count2 = len([e for e in proj.events if e["type"] == "page_view"])
        assert count1 == count2  # no duplicate

        await server._handle_ws_message(ws, {"type": "viewer_state", "state": {"page": 6}}, proj)
        count3 = len([e for e in proj.events if e["type"] == "page_view"])
        assert count3 == count2 + 1  # new page = new event

    @pytest.mark.asyncio
    async def test_file_edit_dedup(self, server):
        """Test file_edit events are deduplicated."""
        proj = server._single
        proj._last_file_edit = {}

        ws = MagicMock()
        ws.send_json = AsyncMock()

        await server._handle_ws_message(
            ws, {"type": "editor_state", "state": {"file": "a.tex", "line": 1}}, proj,
        )
        count1 = len([e for e in proj.events if e["type"] == "file_edit"])
        await server._handle_ws_message(
            ws, {"type": "editor_state", "state": {"file": "a.tex", "line": 1}}, proj,
        )
        count2 = len([e for e in proj.events if e["type"] == "file_edit"])
        assert count1 == count2

        await server._handle_ws_message(
            ws, {"type": "editor_state", "state": {"file": "a.tex", "line": 2}}, proj,
        )
        count3 = len([e for e in proj.events if e["type"] == "file_edit"])
        assert count3 == count2 + 1

    @pytest.mark.asyncio
    async def test_ring_buffer_overflow(self, server):
        """Test ring buffer drops oldest events when full."""
        proj = server._single
        for i in range(250):
            proj.log_event("test_event", index=i)
        assert len(proj.events) == 200
        # Oldest should be index=50
        assert proj.events[0]["index"] == 50

    @pytest.mark.asyncio
    async def test_global_events(self, server):
        """Test global events receive per-project events."""
        proj = server._single
        proj.log_event("test_event", data="hello")
        assert len(server._global_events) == 1
        assert server._global_events[0]["type"] == "test_event"

    @pytest.mark.asyncio
    async def test_source_read_logs_event(self, client, server):
        """Test GET /source logs source_read event."""
        await client.get("/source")
        events = [e for e in server._single.events if e["type"] == "source_read"]
        assert len(events) == 1
        assert events[0]["file"] == "main.tex"


# ---------------------------------------------------------------------------
# Test: File Snapshots
# ---------------------------------------------------------------------------


class TestFileSnapshots:
    """Tests for file snapshot feature."""

    @pytest.fixture
    def config(self, tmp_path):
        main_file = tmp_path / "main.tex"
        main_file.write_text("original content")
        return Config(
            main="main.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=tmp_path / ".texwatch.yaml",
        )

    @pytest.fixture
    def server(self, config):
        return TexWatchServer(config)

    @pytest.fixture
    async def client(self, server):
        async with TestClient(TestServer(server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_no_snapshots_initially(self, server):
        """Test no snapshots at start."""
        assert len(server._single.file_snapshots) == 0

    @pytest.mark.asyncio
    async def test_write_source_stashes_old_content(self, client, server):
        """Test writing source creates a snapshot of old content."""
        resp = await client.post(
            "/source",
            json={"file": "main.tex", "content": "new content"},
        )
        assert resp.status == 200
        assert len(server._single.file_snapshots) == 1
        snap = server._single.file_snapshots[0]
        assert snap["file"] == "main.tex"
        assert snap["content"] == "original content"

    @pytest.mark.asyncio
    async def test_history_endpoint_returns_snapshots(self, client, server):
        """Test GET /p/{name}/snapshots/{file} returns snapshots newest-first."""
        name = server._single.name
        # Write twice
        await client.post("/source", json={"file": "main.tex", "content": "v2"})
        await client.post("/source", json={"file": "main.tex", "content": "v3"})

        resp = await client.get(f"/p/{name}/snapshots/main.tex")
        assert resp.status == 200
        data = await resp.json()
        assert data["file"] == "main.tex"
        assert len(data["snapshots"]) == 2
        # Newest first
        assert data["snapshots"][0]["content"] == "v2"
        assert data["snapshots"][1]["content"] == "original content"

    @pytest.mark.asyncio
    async def test_snapshot_ring_buffer_capped(self, client, server):
        """Test snapshots ring buffer is capped at 20."""
        for i in range(25):
            await client.post(
                "/source",
                json={"file": "main.tex", "content": f"version {i}"},
            )
        assert len(server._single.file_snapshots) == 20

    @pytest.mark.asyncio
    async def test_unprefixed_snapshots_route(self, client, server):
        """Test GET /snapshots/{file} resolves to single project."""
        await client.post("/source", json={"file": "main.tex", "content": "v2"})
        resp = await client.get("/snapshots/main.tex")
        assert resp.status == 200
        data = await resp.json()
        assert data["file"] == "main.tex"
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["content"] == "original content"


# ---------------------------------------------------------------------------
# Semantic extraction endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def rich_config(tmp_path):
    """Config with .bib, environments, and metadata in the main.tex."""
    main_file = tmp_path / "main.tex"
    main_file.write_text(
        "\\documentclass[12pt]{article}\n"
        "\\usepackage{amsmath}\n"
        "\\title{Test Paper}\n"
        "\\author{Test Author}\n"
        "\\newcommand{\\R}{\\mathbb{R}}\n"
        "\\begin{document}\n"
        "\\begin{abstract}\n"
        "We study things.\n"
        "\\end{abstract}\n"
        "\\section{Introduction}\n"
        "See \\cite{knuth1984}.\n"
        "\\begin{theorem}[Main Result]\n"
        "\\label{thm:main}\n"
        "Statement.\n"
        "\\end{theorem}\n"
        "\\begin{equation}\n"
        "\\label{eq:euler}\n"
        "e^{i\\pi}+1=0\n"
        "\\end{equation}\n"
        "\\end{document}\n"
    )
    bib_file = tmp_path / "refs.bib"
    bib_file.write_text(
        "@article{knuth1984,\n"
        "  author = {Donald Knuth},\n"
        "  title = {Literate Programming},\n"
        "  year = {1984},\n"
        "}\n"
        "@book{unused2020,\n"
        "  author = {Nobody},\n"
        "  title = {Unused},\n"
        "  year = {2020},\n"
        "}\n"
    )
    return Config(
        main="main.tex",
        watch=["*.tex"],
        ignore=[],
        compiler="latexmk",
        port=0,
        config_path=tmp_path / ".texwatch.yaml",
    )


@pytest.fixture
def rich_server(rich_config):
    return TexWatchServer(rich_config)


@pytest.fixture
async def rich_client(rich_server):
    async with TestClient(TestServer(rich_server.app)) as client:
        yield client


class TestBibliographyEndpoint:
    """Tests for GET /bibliography endpoint."""

    @pytest.mark.asyncio
    async def test_bibliography_returns_entries(self, rich_client):
        resp = await rich_client.get("/bibliography")
        assert resp.status == 200
        data = await resp.json()
        assert "entries" in data
        assert "citations" in data
        assert "uncited_keys" in data
        assert "undefined_keys" in data
        keys = {e["key"] for e in data["entries"]}
        assert "knuth1984" in keys

    @pytest.mark.asyncio
    async def test_bibliography_uncited(self, rich_client):
        resp = await rich_client.get("/bibliography")
        data = await resp.json()
        assert "unused2020" in data["uncited_keys"]

    @pytest.mark.asyncio
    async def test_bibliography_citations(self, rich_client):
        resp = await rich_client.get("/bibliography")
        data = await resp.json()
        cited_keys = []
        for c in data["citations"]:
            cited_keys.extend(c["keys"])
        assert "knuth1984" in cited_keys

    @pytest.mark.asyncio
    async def test_bibliography_empty(self, client):
        """Empty project returns empty lists."""
        resp = await client.get("/bibliography")
        assert resp.status == 200
        data = await resp.json()
        assert data["entries"] == []


class TestEnvironmentsEndpoint:
    """Tests for GET /environments endpoint."""

    @pytest.mark.asyncio
    async def test_environments_returns_list(self, rich_client):
        resp = await rich_client.get("/environments")
        assert resp.status == 200
        data = await resp.json()
        assert "environments" in data
        types = {e["env_type"] for e in data["environments"]}
        assert "theorem" in types
        assert "equation" in types

    @pytest.mark.asyncio
    async def test_environments_have_labels(self, rich_client):
        resp = await rich_client.get("/environments")
        data = await resp.json()
        labels = {e["label"] for e in data["environments"] if e["label"]}
        assert "thm:main" in labels
        assert "eq:euler" in labels

    @pytest.mark.asyncio
    async def test_environments_have_names(self, rich_client):
        resp = await rich_client.get("/environments")
        data = await resp.json()
        thm = [e for e in data["environments"] if e["env_type"] == "theorem"][0]
        assert thm["name"] == "Main Result"

    @pytest.mark.asyncio
    async def test_environments_empty(self, client):
        resp = await client.get("/environments")
        assert resp.status == 200
        data = await resp.json()
        assert data["environments"] == []


class TestDigestEndpoint:
    """Tests for GET /digest endpoint."""

    @pytest.mark.asyncio
    async def test_digest_returns_metadata(self, rich_client):
        resp = await rich_client.get("/digest")
        assert resp.status == 200
        data = await resp.json()
        assert data["documentclass"] == "article"
        assert data["title"] == "Test Paper"
        assert data["author"] == "Test Author"

    @pytest.mark.asyncio
    async def test_digest_class_options(self, rich_client):
        resp = await rich_client.get("/digest")
        data = await resp.json()
        assert "12pt" in data["class_options"]

    @pytest.mark.asyncio
    async def test_digest_packages(self, rich_client):
        resp = await rich_client.get("/digest")
        data = await resp.json()
        names = [p["name"] for p in data["packages"]]
        assert "amsmath" in names

    @pytest.mark.asyncio
    async def test_digest_commands(self, rich_client):
        resp = await rich_client.get("/digest")
        data = await resp.json()
        assert len(data["commands"]) >= 1
        cmd_names = [c["name"] for c in data["commands"]]
        assert "\\R" in cmd_names

    @pytest.mark.asyncio
    async def test_digest_abstract(self, rich_client):
        resp = await rich_client.get("/digest")
        data = await resp.json()
        assert data["abstract"] is not None
        assert "We study" in data["abstract"]

    @pytest.mark.asyncio
    async def test_digest_empty(self, client):
        resp = await client.get("/digest")
        assert resp.status == 200
        data = await resp.json()
        assert data["documentclass"] == "article"


class TestStructureEndpointExtended:
    """Tests for extended structure response with section_stats and summary."""

    @pytest.mark.asyncio
    async def test_structure_includes_section_stats(self, rich_client):
        resp = await rich_client.get("/structure")
        assert resp.status == 200
        data = await resp.json()
        assert "section_stats" in data
        assert "summary" in data

    @pytest.mark.asyncio
    async def test_structure_summary_fields(self, rich_client):
        resp = await rich_client.get("/structure")
        data = await resp.json()
        summary = data["summary"]
        assert "total_figures" in summary
        assert "total_tables" in summary
        assert "total_equations" in summary
        assert "total_citations" in summary
        assert "total_todos" in summary


# ---------------------------------------------------------------------------
# Phase 3B: Coverage gap tests
# ---------------------------------------------------------------------------


class TestBibliographyParseFailure:
    """Tests for bibliography endpoint exception handling."""

    @pytest.mark.asyncio
    async def test_bibliography_parse_exception(self, client):
        """Test /bibliography returns empty lists when parse raises."""
        with patch("texwatch.server.parse_bibliography", side_effect=RuntimeError("parse error")):
            resp = await client.get("/bibliography")
        assert resp.status == 200
        data = await resp.json()
        assert data["entries"] == []
        assert data["citations"] == []
        assert data["uncited_keys"] == []
        assert data["undefined_keys"] == []


class TestEnvironmentsParseFailure:
    """Tests for environments endpoint exception handling."""

    @pytest.mark.asyncio
    async def test_environments_parse_exception(self, client):
        """Test /environments returns empty list when parse raises."""
        with patch("texwatch.server.parse_environments", side_effect=RuntimeError("parse error")):
            resp = await client.get("/environments")
        assert resp.status == 200
        data = await resp.json()
        assert data["environments"] == []


class TestDigestParseFailure:
    """Tests for digest endpoint exception handling."""

    @pytest.mark.asyncio
    async def test_digest_parse_exception(self, client):
        """Test /digest returns default digest when parse raises."""
        with patch("texwatch.server.parse_digest", side_effect=RuntimeError("parse error")):
            resp = await client.get("/digest")
        assert resp.status == 200
        data = await resp.json()
        # Should return default Digest() fields
        assert data["documentclass"] is None
        assert data["title"] is None


class TestActivityInvalidLimit:
    """Tests for /activity with invalid limit parameter."""

    @pytest.fixture
    def config(self, tmp_path):
        main_file = tmp_path / "main.tex"
        main_file.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
        )
        return Config(
            main="main.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=tmp_path / ".texwatch.yaml",
        )

    @pytest.fixture
    def server(self, config):
        return TexWatchServer(config)

    @pytest.fixture
    async def client(self, server):
        async with TestClient(TestServer(server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_activity_invalid_limit_uses_default(self, client, server):
        """Test /activity with non-numeric limit falls back to default 50."""
        # Log some events so we can verify results
        server._single.log_event("test", data="hello")
        resp = await client.get("/activity?limit=abc")
        assert resp.status == 200
        data = await resp.json()
        # Should still return events (not error)
        assert "events" in data
        assert len(data["events"]) >= 1


class TestClickHandlerEdgeCases:
    """Tests for click WebSocket message edge cases."""

    @pytest.mark.asyncio
    async def test_click_no_synctex_data(self, client, server):
        """Test click message when no synctex data logs skip message."""
        server._synctex_data = None

        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state
            await ws.send_json({
                "type": "click",
                "page": 1,
                "x": 100.0,
                "y": 500.0,
            })
            # Give server time to process
            import asyncio
            await asyncio.sleep(0.1)

        # Verify event was logged
        click_events = [e for e in server._single.events if e["type"] == "click"]
        assert len(click_events) == 1

    @pytest.mark.asyncio
    async def test_click_synctex_miss(self, client, server):
        """Test click message when synctex data exists but page_to_source returns None."""
        from texwatch.synctex import SyncTeXData

        server._synctex_data = SyncTeXData(
            pdf_to_source={},  # empty - page_to_source will return None
            source_to_pdf={},
            input_files={},
        )

        async with client.ws_connect("/ws") as ws:
            await ws.receive_json()  # initial state
            await ws.send_json({
                "type": "click",
                "page": 1,
                "x": 100.0,
                "y": 500.0,
            })
            import asyncio
            await asyncio.sleep(0.1)

        # Event was logged even when synctex missed
        click_events = [e for e in server._single.events if e["type"] == "click"]
        assert len(click_events) == 1


class TestCaptureEmptyPdf:
    """Tests for capture with empty PDF (0 pages)."""

    @pytest.mark.asyncio
    async def test_capture_empty_pdf(self, client, config):
        """Test capture returns error for PDF with 0 pages."""
        import pymupdf as _pymupdf

        # Create a real 1-page PDF so the file exists (pymupdf can't save 0-page PDFs)
        pdf_path = config.config_path.parent / "main.pdf"
        doc = _pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        # Mock pymupdf.open to return a document with len() == 0
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=0)

        with patch("pymupdf.open", return_value=mock_doc):
            resp = await client.get("/capture")
        assert resp.status == 400
        data = await resp.json()
        assert "no pages" in data["error"].lower()
        mock_doc.close.assert_called_once()


class TestPostSourceInvalidJson:
    """Tests for POST /source with invalid JSON."""

    @pytest.mark.asyncio
    async def test_post_source_invalid_json(self, client):
        """Test POST /source with non-JSON body returns 400."""
        resp = await client.post(
            "/source",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid JSON" in data["error"]


class TestSetCurrentInvalidJson:
    """Tests for POST /current with invalid JSON."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nB\n\\end{document}\n")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")
        return [("a", config_a), ("b", config_b)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_post_current_invalid_json(self, multi_client):
        """Test POST /current with non-JSON body returns 400."""
        resp = await multi_client.post(
            "/current",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid JSON" in data["error"]

    @pytest.mark.asyncio
    async def test_post_current_missing_project_clears(self, multi_client):
        """Test POST /current without project field clears current."""
        resp = await multi_client.post("/current", json={})
        assert resp.status == 200
        data = await resp.json()
        assert data["current"] is None


class TestMultiProjectHistoryError:
    """Tests for /history on multi-project server without current."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("A\n")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("B\n")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")
        return [("a", config_a), ("b", config_b)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_history_multi_project_no_current(self, multi_client):
        """Test /snapshots/{file} on multi-project server without current returns 400."""
        resp = await multi_client.get("/snapshots/main.tex")
        assert resp.status == 400
        data = await resp.json()
        assert "projects" in data


class TestMultiProjectConvenienceProxies:
    """Tests for multi-project convenience proxy no-ops."""

    def test_multi_last_result_is_none(self, tmp_path):
        """Test _last_result returns None for multi-project."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("hi")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("hi")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")
        server = TexWatchServer(projects=[("a", config_a), ("b", config_b)])
        assert server._last_result is None
        assert server._synctex_data is None
        assert server._editor_state == {"file": None, "line": None}
        assert server._viewer_state == {"page": 1, "total_pages": 0, "visible_lines": None}
        assert server._watcher is None

    def test_multi_setters_are_noop(self, tmp_path):
        """Test setters on multi-project server are no-ops."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("hi")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("hi")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")
        server = TexWatchServer(projects=[("a", config_a), ("b", config_b)])

        # These should not raise (they're no-ops on multi-project)
        server._compiling = True
        server._last_result = None
        server._synctex_data = None
        server._viewer_state = {"page": 5}
        server._editor_state = {"file": "x.tex", "line": 1}
        server._watcher = None

    @pytest.mark.asyncio
    async def test_multi_broadcast_noop(self, tmp_path):
        """Test _broadcast is a no-op on multi-project server."""
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text("hi")
        config_a = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_a / ".texwatch.yaml")
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text("hi")
        config_b = Config(main="main.tex", watch=["*.tex"], ignore=[], compiler="latexmk",
                          port=0, config_path=dir_b / ".texwatch.yaml")
        server = TexWatchServer(projects=[("a", config_a), ("b", config_b)])
        # Should not raise
        await server._broadcast({"type": "test"})
        await server._do_compile()
        await server._on_file_change("/tmp/test.tex")
        await server._send_state(MagicMock())


class TestServerNoConfigOrProjects:
    """Test server constructor validation."""

    def test_server_requires_config_or_projects(self):
        """Test that TexWatchServer raises ValueError without config or projects."""
        with pytest.raises(ValueError, match="Must provide config or projects"):
            TexWatchServer()


class TestDashboardEndpoint:
    """Tests for GET /dashboard."""

    @pytest.mark.asyncio
    async def test_dashboard_returns_all_sections(self, client, config):
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text(
            "\\documentclass{article}\n\\title{Test}\n"
            "\\begin{document}\n\\section{Intro}\nHello.\n\\end{document}\n"
        )
        resp = await client.get("/dashboard")
        assert resp.status == 200
        data = await resp.json()
        for key in ("health", "sections", "issues", "bibliography", "changes",
                    "environments", "context", "files", "activity"):
            assert key in data

    @pytest.mark.asyncio
    async def test_dashboard_health_fields(self, client, config):
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text("\\documentclass{article}\n\\title{Test Paper}\n\\begin{document}\n\\end{document}\n")
        resp = await client.get("/dashboard")
        data = await resp.json()
        health = data["health"]
        assert health["title"] == "Test Paper"
        assert health["documentclass"] == "article"
        assert "compile_status" in health
        assert "error_count" in health

    @pytest.mark.asyncio
    async def test_dashboard_per_project(self, client, config):
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
        resp = await client.get("/p/test/dashboard")
        # The project name from the fixture is based on the main file stem
        # Let's check: if 404, the name is different
        if resp.status == 404:
            # Get the actual project name
            projects_resp = await client.get("/projects")
            projects_data = await projects_resp.json()
            name = projects_data["projects"][0]["name"]
            resp = await client.get(f"/p/{name}/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert "health" in data

    @pytest.mark.asyncio
    async def test_dashboard_issues_include_todos(self, client, config):
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "% TODO: fix this\n\\end{document}\n"
        )
        resp = await client.get("/dashboard")
        data = await resp.json()
        todo_issues = [i for i in data["issues"] if i["type"] == "todo"]
        assert len(todo_issues) >= 1

    @pytest.mark.asyncio
    async def test_dashboard_environments(self, client, config):
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{theorem}[Main]\n\\label{thm:main}\nStatement.\n\\end{theorem}\n"
            "\\end{document}\n"
        )
        resp = await client.get("/dashboard")
        data = await resp.json()
        assert "items" in data["environments"]

    @pytest.mark.asyncio
    async def test_dashboard_bibliography(self, client, config):
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "\\cite{foo2024}\n\\end{document}\n"
        )
        resp = await client.get("/dashboard")
        data = await resp.json()
        bib = data["bibliography"]
        assert "defined" in bib
        assert "cited" in bib
        assert "undefined_keys" in bib

    @pytest.mark.asyncio
    async def test_dashboard_includes_context(self, client, config):
        """Dashboard response includes editor/viewer context with defaults."""
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n")
        resp = await client.get("/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert "context" in data
        ctx = data["context"]
        assert "editor" in ctx
        assert "viewer" in ctx
        # No editor state set — section should be None
        assert ctx["editor"]["section"] is None

    @pytest.mark.asyncio
    async def test_dashboard_includes_files(self, client, config):
        """Dashboard response includes file tree with project files."""
        project_dir = config.config_path.parent
        main = project_dir / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
        resp = await client.get("/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert "files" in data
        assert isinstance(data["files"], list)
        # main.tex should appear in the file tree
        names = [f["name"] for f in data["files"] if f.get("type") == "file"]
        assert "main.tex" in names


class TestCompilesEndpoint:
    @pytest.mark.asyncio
    async def test_compiles_returns_200(self, client):
        resp = await client.get("/compiles")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_compiles_returns_list(self, client):
        resp = await client.get("/compiles")
        data = await resp.json()
        assert isinstance(data, list)


class TestSnapshotsEndpointRename:
    @pytest.mark.asyncio
    async def test_snapshots_endpoint_exists(self, client):
        resp = await client.get("/snapshots/main.tex")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_dashboard_includes_activity(self, client, config):
        """Dashboard response includes recent activity events."""
        resp = await client.get("/dashboard")
        assert resp.status == 200
        data = await resp.json()
        assert "activity" in data
        assert isinstance(data["activity"], list)


class TestCurrentEndpointUnset:
    """Tests for POST /current with null to clear the current project."""

    @pytest.fixture
    def multi_config(self, tmp_path):
        dir_a = tmp_path / "pa"
        dir_a.mkdir()
        (dir_a / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nA\n\\end{document}\n"
        )
        config_a = Config(
            main="main.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=dir_a / ".texwatch.yaml",
        )
        dir_b = tmp_path / "pb"
        dir_b.mkdir()
        (dir_b / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nB\n\\end{document}\n"
        )
        config_b = Config(
            main="main.tex", watch=["*.tex"], ignore=[],
            compiler="latexmk", port=0,
            config_path=dir_b / ".texwatch.yaml",
        )
        return [("alpha", config_a), ("beta", config_b)]

    @pytest.fixture
    def multi_server(self, multi_config):
        return TexWatchServer(projects=multi_config)

    @pytest.fixture
    async def multi_client(self, multi_server):
        async with TestClient(TestServer(multi_server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_post_current_null_clears(self, multi_client, multi_server):
        """Test POST /current with project=null clears the current project."""
        multi_server._current_project_name = "alpha"
        resp = await multi_client.post("/current", json={"project": None})
        assert resp.status == 200
        data = await resp.json()
        assert data["current"] is None
        assert multi_server._current_project_name is None

    @pytest.mark.asyncio
    async def test_post_current_empty_string_clears(self, multi_client, multi_server):
        """Test POST /current with project='' clears the current project."""
        multi_server._current_project_name = "beta"
        resp = await multi_client.post("/current", json={"project": ""})
        assert resp.status == 200
        data = await resp.json()
        assert data["current"] is None
        assert multi_server._current_project_name is None

    @pytest.mark.asyncio
    async def test_clear_then_get_shows_null(self, multi_client, multi_server):
        """Test that clearing current and then GET /current shows null."""
        multi_server._current_project_name = "alpha"
        await multi_client.post("/current", json={"project": None})
        resp = await multi_client.get("/current")
        data = await resp.json()
        assert data["current"] is None
        assert "projects" in data

    @pytest.mark.asyncio
    async def test_unprefixed_goto_fails_after_clear(self, multi_client, multi_server):
        """Test that unprefixed goto fails after clearing current."""
        multi_server._current_project_name = "alpha"
        await multi_client.post("/current", json={"project": None})
        resp = await multi_client.post("/goto", json={"page": 1})
        assert resp.status == 400


class TestMcpRegistration:
    """Tests for automatic .mcp.json registration."""

    def test_register_mcp_creates_file(self, tmp_path):
        """_register_mcp creates .mcp.json with texwatch entry."""
        from texwatch.server import _register_mcp
        _register_mcp(8765, tmp_path)
        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        data = json.loads(mcp_file.read_text())
        assert "mcpServers" in data
        assert "texwatch" in data["mcpServers"]
        entry = data["mcpServers"]["texwatch"]
        assert entry["command"] == "texwatch"
        assert "--port" in entry["args"]
        assert "8765" in entry["args"]

    def test_register_mcp_preserves_existing(self, tmp_path):
        """_register_mcp preserves other entries in .mcp.json."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"other-tool": {"command": "other"}}
        }))
        from texwatch.server import _register_mcp
        _register_mcp(9000, tmp_path)
        data = json.loads(mcp_file.read_text())
        assert "other-tool" in data["mcpServers"]
        assert "texwatch" in data["mcpServers"]

    def test_register_mcp_updates_port(self, tmp_path):
        """_register_mcp updates port if .mcp.json already has texwatch."""
        from texwatch.server import _register_mcp
        _register_mcp(8765, tmp_path)
        _register_mcp(9000, tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "9000" in str(data["mcpServers"]["texwatch"]["args"])

    def test_unregister_mcp_removes_entry(self, tmp_path):
        """_unregister_mcp removes texwatch from .mcp.json."""
        from texwatch.server import _register_mcp, _unregister_mcp
        _register_mcp(8765, tmp_path)
        _unregister_mcp(tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "texwatch" not in data["mcpServers"]

    def test_unregister_mcp_preserves_others(self, tmp_path):
        """_unregister_mcp keeps other entries."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {
                "texwatch": {"command": "texwatch", "args": ["mcp"]},
                "other": {"command": "other"},
            }
        }))
        from texwatch.server import _unregister_mcp
        _unregister_mcp(tmp_path)
        data = json.loads(mcp_file.read_text())
        assert "texwatch" not in data["mcpServers"]
        assert "other" in data["mcpServers"]

    def test_unregister_mcp_no_file(self, tmp_path):
        """_unregister_mcp is a no-op if .mcp.json doesn't exist."""
        from texwatch.server import _unregister_mcp
        _unregister_mcp(tmp_path)  # should not raise

    def test_register_mcp_handles_corrupt_json(self, tmp_path):
        """_register_mcp recovers from corrupt .mcp.json."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text("{invalid json!!")
        from texwatch.server import _register_mcp
        _register_mcp(8765, tmp_path)
        data = json.loads(mcp_file.read_text())
        assert "texwatch" in data["mcpServers"]

    def test_unregister_mcp_handles_corrupt_json(self, tmp_path):
        """_unregister_mcp is a no-op on corrupt .mcp.json."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text("{invalid json!!")
        from texwatch.server import _unregister_mcp
        _unregister_mcp(tmp_path)  # should not raise
        # File content unchanged
        assert mcp_file.read_text() == "{invalid json!!"

    def test_register_mcp_write_failure(self, tmp_path):
        """_register_mcp logs warning on write failure instead of crashing."""
        from texwatch.server import _register_mcp
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        _register_mcp(8765, readonly_dir)  # should not raise
        readonly_dir.chmod(0o755)  # cleanup

    def test_unregister_mcp_write_failure(self, tmp_path):
        """_unregister_mcp logs warning on write failure instead of crashing."""
        from texwatch.server import _register_mcp, _unregister_mcp
        _register_mcp(8765, tmp_path)
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.chmod(0o444)
        _unregister_mcp(tmp_path)  # should not raise
        mcp_file.chmod(0o644)  # cleanup


class TestLabelsEndpoint:
    @pytest.mark.asyncio
    async def test_labels_returns_200(self, client):
        resp = await client.get("/labels")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_labels_returns_list(self, client):
        resp = await client.get("/labels")
        data = await resp.json()
        assert isinstance(data, list)

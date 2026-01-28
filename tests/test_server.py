"""Tests for server module."""

import json
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
        config_path=tmp_path / "texwatch.yaml",
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


class TestGotoEndpoint:
    """Tests for POST /goto endpoint."""

    @pytest.mark.asyncio
    async def test_goto_line_no_synctex(self, client, server):
        """Test goto line without SyncTeX data."""
        resp = await client.post(
            "/goto",
            json={"line": 42},
        )
        assert resp.status == 404

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
    async def test_goto_section_not_implemented(self, client):
        """Test goto section returns 501."""
        resp = await client.post(
            "/goto",
            json={"section": "Introduction"},
        )
        assert resp.status == 501


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


class TestCaptureEndpoint:
    """Tests for GET /capture endpoint."""

    @pytest.mark.asyncio
    async def test_capture_no_pdf(self, client, server):
        """Test capture when no PDF exists."""
        resp = await client.get("/capture")
        assert resp.status in (404, 501)


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

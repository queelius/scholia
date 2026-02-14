"""Tests for the MCP server module."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests if mcp/httpx not available
mcp = pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")

from texwatch.mcp_server import _base_url, create_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(text: str = "", status_code: int = 200, content: bytes = b"",
                   content_type: str = "application/json") -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    return resp


async def _call_tool(server, tool_name: str, arguments: dict):
    """Call a tool on the MCP server and return the content list.

    FastMCP.call_tool returns a tuple of (content_list, metadata).
    We return only the content list for simpler assertions.
    """
    result = await server.call_tool(tool_name, arguments)
    # call_tool returns (content_list, metadata_dict)
    if isinstance(result, tuple):
        return result[0]
    return result


# ---------------------------------------------------------------------------
# TestMcpToolUrls — verify each tool constructs the right URL
# ---------------------------------------------------------------------------


class TestMcpToolUrls:
    """Verify each tool constructs the correct URL."""

    def test_base_url_default(self):
        """Test base URL with default port and no project."""
        assert _base_url(8765, None) == "http://localhost:8765"

    def test_base_url_custom_port(self):
        """Test base URL with custom port."""
        assert _base_url(9000, None) == "http://localhost:9000"

    def test_base_url_with_project(self):
        """Test base URL with project name."""
        assert _base_url(8765, "paper1") == "http://localhost:8765/p/paper1"

    def test_base_url_custom_port_with_project(self):
        """Test base URL with custom port and project name."""
        assert _base_url(9000, "paper1") == "http://localhost:9000/p/paper1"

    @pytest.mark.asyncio
    async def test_dashboard_url(self):
        """Test texwatch (unified dashboard) builds correct URL."""
        mock_resp = _mock_response(text='{"health":{}}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client
            server = create_server()
            result = await _call_tool(server, "texwatch", {"port": 8765})
            mock_client.get.assert_called_once_with("http://localhost:8765/dashboard")

    @pytest.mark.asyncio
    async def test_dashboard_url_with_project(self):
        """Test texwatch (unified dashboard) builds correct URL with project."""
        mock_resp = _mock_response(text='{"health":{}}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client
            server = create_server()
            result = await _call_tool(server, "texwatch", {"port": 9000, "project": "paper1"})
            mock_client.get.assert_called_once_with("http://localhost:9000/p/paper1/dashboard")

    @pytest.mark.asyncio
    async def test_compile_url(self):
        """Test texwatch_compile builds correct URL."""
        mock_resp = _mock_response(text='{"success":true}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_compile", {"port": 8765})

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/compile", json={}
            )

    @pytest.mark.asyncio
    async def test_source_url(self):
        """Test texwatch_source builds correct URL."""
        mock_resp = _mock_response(text='{"file":"main.tex","content":"hello"}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_source", {"port": 8765})

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/source", params={}
            )

    @pytest.mark.asyncio
    async def test_source_url_with_file(self):
        """Test texwatch_source builds correct URL with file parameter."""
        mock_resp = _mock_response(text='{"file":"intro.tex","content":"hello"}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_source", {"port": 8765, "file": "intro.tex"})

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/source", params={"file": "intro.tex"}
            )

    @pytest.mark.asyncio
    async def test_write_source_sends_post(self):
        """Test texwatch_write_source sends POST with file and content."""
        mock_resp = _mock_response(text='{"ok":true}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_write_source", {
                "file": "main.tex", "content": "\\documentclass{article}",
                "port": 8765,
            })

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/source",
                json={"file": "main.tex", "content": "\\documentclass{article}"},
            )

    @pytest.mark.asyncio
    async def test_write_source_with_mtime(self):
        """Test texwatch_write_source includes base_mtime_ns when provided."""
        mock_resp = _mock_response(text='{"ok":true}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_write_source", {
                "file": "main.tex", "content": "hello",
                "base_mtime_ns": "1234567890", "port": 8765,
            })

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/source",
                json={"file": "main.tex", "content": "hello", "base_mtime_ns": "1234567890"},
            )

    @pytest.mark.asyncio
    async def test_write_source_with_project(self):
        """Test texwatch_write_source routes to project URL."""
        mock_resp = _mock_response(text='{"ok":true}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_write_source", {
                "file": "main.tex", "content": "hello",
                "port": 8765, "project": "paper1",
            })

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/p/paper1/source",
                json={"file": "main.tex", "content": "hello"},
            )

    @pytest.mark.asyncio
    async def test_history_url(self):
        """Test texwatch_history calls correct URL."""
        mock_resp = _mock_response(text='{"file":"main.tex","snapshots":[]}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_history", {
                "file": "main.tex", "port": 8765,
            })

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/history/main.tex",
            )

    @pytest.mark.asyncio
    async def test_history_with_project(self):
        """Test texwatch_history with project routes correctly."""
        mock_resp = _mock_response(text='{"file":"main.tex","snapshots":[]}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_history", {
                "file": "intro.tex", "port": 8765, "project": "paper1",
            })

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/p/paper1/history/intro.tex",
            )

    @pytest.mark.asyncio
    async def test_project_get_url(self):
        """Test texwatch_project with no project arg calls GET /current."""
        mock_resp = _mock_response(text='{"current":"thesis","projects":["thesis","paper1"]}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_project", {"port": 8765})

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/current",
            )

    @pytest.mark.asyncio
    async def test_project_switch_url(self):
        """Test texwatch_project with project="beta" calls POST /current."""
        mock_resp = _mock_response(text='{"current":"beta"}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_project", {
                "project": "beta", "port": 8765,
            })

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/current",
                json={"project": "beta"},
            )


# ---------------------------------------------------------------------------
# TestMcpToolParameters — verify parameter handling
# ---------------------------------------------------------------------------


class TestMcpToolParameters:
    """Verify parameter handling for tools with complex parameters."""

    @pytest.mark.asyncio
    async def test_goto_with_line(self):
        """Test texwatch_goto with line parameter."""
        mock_resp = _mock_response(text='{"success":true,"page":3}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_goto", {"line": 42, "port": 8765})

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/goto", json={"line": 42}
            )

    @pytest.mark.asyncio
    async def test_goto_with_page(self):
        """Test texwatch_goto with page parameter."""
        mock_resp = _mock_response(text='{"success":true,"page":5}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_goto", {"page": 5, "port": 8765})

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/goto", json={"page": 5}
            )

    @pytest.mark.asyncio
    async def test_goto_with_section(self):
        """Test texwatch_goto with section parameter."""
        mock_resp = _mock_response(text='{"success":true,"section":"Introduction"}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_goto", {"section": "Introduction", "port": 8765})

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/goto", json={"section": "Introduction"}
            )

    @pytest.mark.asyncio
    async def test_goto_no_target_returns_error(self):
        """Test texwatch_goto with no target returns error."""
        server = create_server()
        result = await _call_tool(server, "texwatch_goto", {"port": 8765})

        # Result should contain error text
        assert len(result) > 0
        content_text = result[0].text
        error = json.loads(content_text)
        assert "error" in error
        assert "Exactly one" in error["error"]

    @pytest.mark.asyncio
    async def test_goto_multiple_targets_returns_error(self):
        """Test texwatch_goto with multiple targets returns error."""
        server = create_server()
        result = await _call_tool(server, "texwatch_goto", {"line": 42, "page": 3, "port": 8765})

        assert len(result) > 0
        content_text = result[0].text
        error = json.loads(content_text)
        assert "error" in error

    @pytest.mark.asyncio
    async def test_goto_with_project(self):
        """Test texwatch_goto with project parameter."""
        mock_resp = _mock_response(text='{"success":true}')
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            await _call_tool(server, "texwatch_goto", {
                "line": 10, "port": 8765, "project": "paper1"
            })

            mock_client.post.assert_called_once_with(
                "http://localhost:8765/p/paper1/goto", json={"line": 10}
            )

    @pytest.mark.asyncio
    async def test_capture_default_params(self):
        """Test texwatch_capture with default parameters."""
        mock_resp = _mock_response(
            content=b"\x89PNG\r\n\x1a\n",
            content_type="image/png"
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            result = await _call_tool(server, "texwatch_capture", {"port": 8765})

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/capture", params={"dpi": 150}
            )

    @pytest.mark.asyncio
    async def test_capture_with_page_and_dpi(self):
        """Test texwatch_capture with page and dpi parameters."""
        mock_resp = _mock_response(
            content=b"\x89PNG\r\n\x1a\n",
            content_type="image/png"
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            result = await _call_tool(server, "texwatch_capture", {
                "page": 2, "dpi": 300, "port": 8765
            })

            mock_client.get.assert_called_once_with(
                "http://localhost:8765/capture", params={"page": 2, "dpi": 300}
            )

    @pytest.mark.asyncio
    async def test_capture_returns_image_content(self):
        """Test texwatch_capture returns ImageContent for PNG responses."""
        from mcp.types import ImageContent

        png_data = b"\x89PNG\r\n\x1a\nfakedata"
        mock_resp = _mock_response(
            content=png_data,
            content_type="image/png"
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            result = await _call_tool(server, "texwatch_capture", {"port": 8765})

            assert len(result) == 1
            item = result[0]
            assert isinstance(item, ImageContent)
            assert item.mimeType == "image/png"
            assert item.data == base64.b64encode(png_data).decode("ascii")

    @pytest.mark.asyncio
    async def test_capture_returns_text_on_error(self):
        """Test texwatch_capture returns TextContent for error responses."""
        from mcp.types import TextContent

        mock_resp = _mock_response(
            text='{"error":"PDF not found"}',
            content_type="application/json"
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            server = create_server()
            result = await _call_tool(server, "texwatch_capture", {"port": 8765})

            assert len(result) == 1
            item = result[0]
            assert isinstance(item, TextContent)
            assert "PDF not found" in item.text


# ---------------------------------------------------------------------------
# TestMcpServerCreation — verify the server is configured correctly
# ---------------------------------------------------------------------------


class TestMcpServerCreation:
    """Verify the MCP server object is created with expected tools."""

    def test_server_creation(self):
        """Test that create_server returns a FastMCP instance."""
        from mcp.server.fastmcp import FastMCP

        server = create_server()
        assert isinstance(server, FastMCP)

    def test_server_name(self):
        """Test that the server has the correct name."""
        server = create_server()
        assert server.name == "texwatch"

    @pytest.mark.asyncio
    async def test_server_has_all_tools(self):
        """Test that the server registers all expected tools."""
        server = create_server()
        tools = await server.list_tools()
        tool_names = {t.name for t in tools}

        expected = {
            "texwatch",
            "texwatch_source",
            "texwatch_history",
            "texwatch_goto",
            "texwatch_compile",
            "texwatch_write_source",
            "texwatch_capture",
            "texwatch_project",
        }
        assert tool_names == expected

    @pytest.mark.asyncio
    async def test_tool_descriptions_not_empty(self):
        """Test that all tools have descriptions."""
        server = create_server()
        tools = await server.list_tools()

        for tool in tools:
            assert tool.description, f"Tool {tool.name} has no description"

    @pytest.mark.asyncio
    async def test_texwatch_tool_schema(self):
        """Test that texwatch has port and project params in schema."""
        server = create_server()
        tools = await server.list_tools()
        tool = next(t for t in tools if t.name == "texwatch")

        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "port" in props
        assert "project" in props

    @pytest.mark.asyncio
    async def test_goto_tool_schema(self):
        """Test that texwatch_goto has line, page, section, port, project params."""
        server = create_server()
        tools = await server.list_tools()
        goto_tool = next(t for t in tools if t.name == "texwatch_goto")

        schema = goto_tool.inputSchema
        props = schema.get("properties", {})
        assert "line" in props
        assert "page" in props
        assert "section" in props
        assert "port" in props
        assert "project" in props

    @pytest.mark.asyncio
    async def test_capture_tool_schema(self):
        """Test that texwatch_capture has page, dpi, port, project params."""
        server = create_server()
        tools = await server.list_tools()
        capture_tool = next(t for t in tools if t.name == "texwatch_capture")

        schema = capture_tool.inputSchema
        props = schema.get("properties", {})
        assert "page" in props
        assert "dpi" in props
        assert "port" in props
        assert "project" in props

    @pytest.mark.asyncio
    async def test_source_tool_schema(self):
        """Test that texwatch_source has file, port, project params."""
        server = create_server()
        tools = await server.list_tools()
        source_tool = next(t for t in tools if t.name == "texwatch_source")

        schema = source_tool.inputSchema
        props = schema.get("properties", {})
        assert "file" in props
        assert "port" in props
        assert "project" in props

    @pytest.mark.asyncio
    async def test_write_source_tool_schema(self):
        """Test that texwatch_write_source has file, content, base_mtime_ns, port, project params."""
        server = create_server()
        tools = await server.list_tools()
        tool = next(t for t in tools if t.name == "texwatch_write_source")

        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "file" in props
        assert "content" in props
        assert "base_mtime_ns" in props
        assert "port" in props
        assert "project" in props
        # file and content should be required
        required = schema.get("required", [])
        assert "file" in required
        assert "content" in required

    @pytest.mark.asyncio
    async def test_project_tool_schema(self):
        """Test that texwatch_project has project and port params."""
        server = create_server()
        tools = await server.list_tools()
        tool = next(t for t in tools if t.name == "texwatch_project")

        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "project" in props
        assert "port" in props

    @pytest.mark.asyncio
    async def test_history_tool_schema(self):
        """Test that texwatch_history has file, port, and project params."""
        server = create_server()
        tools = await server.list_tools()
        tool = next(t for t in tools if t.name == "texwatch_history")

        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "file" in props
        assert "port" in props
        assert "project" in props
        required = schema.get("required", [])
        assert "file" in required


# ---------------------------------------------------------------------------
# TestCliMcpSubcommand — verify CLI integration
# ---------------------------------------------------------------------------


class TestCliMcpSubcommand:
    """Test the mcp CLI subcommand integration."""

    def test_mcp_subcommand_parsed(self):
        """Test that mcp subcommand parses correctly."""
        from texwatch.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["mcp"])
        assert args.command == "mcp"

    def test_mcp_subcommand_default_port(self):
        """Test mcp subcommand has default port."""
        from texwatch.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["mcp"])
        assert args.port == 8765

    def test_mcp_subcommand_custom_port(self):
        """Test mcp subcommand with custom port."""
        from texwatch.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["mcp", "-p", "9000"])
        assert args.port == 9000

    def test_mcp_subcommand_with_project(self):
        """Test mcp subcommand with project option."""
        from texwatch.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["mcp", "--project", "paper1"])
        assert args.project == "paper1"

    def test_mcp_in_dispatch(self):
        """Test that mcp is registered in _DISPATCH."""
        from texwatch.cli import _DISPATCH, cmd_mcp

        assert "mcp" in _DISPATCH
        assert _DISPATCH["mcp"] is cmd_mcp

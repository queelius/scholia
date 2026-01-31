"""MCP (Model Context Protocol) server for texwatch.

Bridges Claude Code to the texwatch HTTP API using stdio transport.
Each tool connects to the running texwatch server via HTTP.

Requires: pip install "mcp>=1.0" httpx
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys

try:
    import httpx
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def _check_deps() -> None:
    """Raise a friendly error if mcp/httpx are not installed."""
    if not HAS_DEPS:
        print(
            "Error: MCP server requires 'mcp' and 'httpx' packages.\n"
            "Install them with:\n"
            "  pip install 'mcp>=1.0' httpx\n"
            "Or install the optional dependency group:\n"
            "  pip install texwatch[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)


def _base_url(port: int, project: str | None) -> str:
    """Build the base URL for the texwatch HTTP API."""
    base = f"http://localhost:{port}"
    if project:
        base += f"/p/{project}"
    return base


def create_server() -> "FastMCP":
    """Create and configure the MCP server with all tools."""
    _check_deps()

    mcp = FastMCP("texwatch")

    @mcp.tool()
    async def texwatch_status(port: int = 8765, project: str | None = None) -> str:
        """Get compilation status, errors, and warnings from the running texwatch instance."""
        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/status")
            return resp.text

    @mcp.tool()
    async def texwatch_context(port: int = 8765, project: str | None = None) -> str:
        """Get what the user is currently looking at: editor position, viewer page, current section, and compile status."""
        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/context")
            return resp.text

    @mcp.tool()
    async def texwatch_errors(port: int = 8765, project: str | None = None) -> str:
        """Get compilation errors with source context lines."""
        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/errors")
            return resp.text

    @mcp.tool()
    async def texwatch_structure(port: int = 8765, project: str | None = None) -> str:
        """Get paper outline: sections, TODOs, input files, and word count."""
        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/structure")
            return resp.text

    @mcp.tool()
    async def texwatch_goto(
        line: int | None = None,
        page: int | None = None,
        section: str | None = None,
        port: int = 8765,
        project: str | None = None,
    ) -> str:
        """Navigate the PDF viewer to a specific line, page, or section. Exactly one of line, page, or section must be provided."""
        # Validate: exactly one target must be specified
        targets = [x for x in (line, page, section) if x is not None]
        if len(targets) != 1:
            return json.dumps({"error": "Exactly one of line, page, or section must be provided"})

        data: dict = {}
        if line is not None:
            data["line"] = line
        elif page is not None:
            data["page"] = page
        elif section is not None:
            data["section"] = section

        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{base}/goto", json=data)
            return resp.text

    @mcp.tool()
    async def texwatch_compile(port: int = 8765, project: str | None = None) -> str:
        """Trigger recompilation of the TeX document."""
        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{base}/compile", json={})
            return resp.text

    @mcp.tool()
    async def texwatch_capture(
        page: int | None = None,
        dpi: int = 150,
        port: int = 8765,
        project: str | None = None,
    ) -> list[TextContent | ImageContent]:
        """Screenshot current PDF page as PNG image. Returns the image as base64-encoded data."""
        params: dict = {}
        if page is not None:
            params["page"] = page
        params["dpi"] = dpi

        base = _base_url(port, project)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base}/capture", params=params)

            if resp.headers.get("content-type", "").startswith("image/"):
                b64_data = base64.b64encode(resp.content).decode("ascii")
                return [
                    ImageContent(
                        type="image",
                        data=b64_data,
                        mimeType="image/png",
                    )
                ]
            else:
                # Error response (JSON)
                return [TextContent(type="text", text=resp.text)]

    @mcp.tool()
    async def texwatch_source(
        file: str | None = None,
        port: int = 8765,
        project: str | None = None,
    ) -> str:
        """Read source file content from the texwatch project. If no file is specified, reads the main file."""
        params: dict = {}
        if file is not None:
            params["file"] = file

        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/source", params=params)
            return resp.text

    return mcp


async def run_async() -> None:
    """Run the MCP server with stdio transport."""
    mcp = create_server()
    await mcp.run_stdio_async()


def main(port: int = 8765, project: str | None = None) -> None:
    """Entry point for the MCP server.

    The port and project parameters are available as defaults for the tools,
    but each tool also accepts them as explicit parameters.
    """
    _check_deps()
    mcp = create_server()
    asyncio.run(mcp.run_stdio_async())

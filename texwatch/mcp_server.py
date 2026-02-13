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


async def _get(endpoint: str, port: int, project: str | None = None) -> str:
    """GET a texwatch endpoint and return the response text."""
    base = _base_url(port, project)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base}{endpoint}")
        return resp.text


def create_server() -> "FastMCP":
    """Create and configure the MCP server with all tools."""
    _check_deps()

    mcp = FastMCP("texwatch")

    @mcp.tool()
    async def texwatch_status(port: int = 8765, project: str | None = None) -> str:
        """Get compilation status, errors, and warnings from the running texwatch instance."""
        return await _get("/status", port, project)

    @mcp.tool()
    async def texwatch_context(port: int = 8765, project: str | None = None) -> str:
        """Get what the user is currently looking at: editor position, viewer page, current section, and compile status."""
        return await _get("/context", port, project)

    @mcp.tool()
    async def texwatch_errors(port: int = 8765, project: str | None = None) -> str:
        """Get compilation errors with source context lines."""
        return await _get("/errors", port, project)

    @mcp.tool()
    async def texwatch_structure(port: int = 8765, project: str | None = None) -> str:
        """Get paper outline: sections, TODOs, input files, and word count."""
        return await _get("/structure", port, project)

    @mcp.tool()
    async def texwatch_goto(
        line: int | None = None,
        page: int | None = None,
        section: str | None = None,
        port: int = 8765,
        project: str | None = None,
    ) -> str:
        """Navigate the PDF viewer to a specific line, page, or section. Exactly one of line, page, or section must be provided."""
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
        params: dict = {"dpi": dpi}
        if page is not None:
            params["page"] = page

        base = _base_url(port, project)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base}/capture", params=params)

            if resp.headers.get("content-type", "").startswith("image/"):
                b64_data = base64.b64encode(resp.content).decode("ascii")
                return [ImageContent(type="image", data=b64_data, mimeType="image/png")]
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

    @mcp.tool()
    async def texwatch_write_source(
        file: str,
        content: str,
        base_mtime_ns: str | None = None,
        port: int = 8765,
        project: str | None = None,
    ) -> str:
        """Write content to a source file. Provide base_mtime_ns for conflict detection."""
        base = _base_url(port, project)
        data: dict = {"file": file, "content": content}
        if base_mtime_ns is not None:
            data["base_mtime_ns"] = base_mtime_ns
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{base}/source", json=data)
            return resp.text

    @mcp.tool()
    async def texwatch_files(port: int = 8765, project: str | None = None) -> str:
        """List all project source files (TeX, BibTeX, etc.)."""
        return await _get("/files", port, project)

    @mcp.tool()
    async def texwatch_activity(
        limit: int = 50,
        type: str | None = None,
        port: int = 8765,
        project: str | None = None,
    ) -> str:
        """Get recent activity events: compilations, navigations, edits, page views. Filter by type (compile_start, compile_finish, goto, page_view, file_edit, capture, source_read, source_write, click)."""
        base = _base_url(port, project)
        params: dict = {"limit": limit}
        if type is not None:
            params["type"] = type
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/activity", params=params)
            return resp.text

    @mcp.tool()
    async def texwatch_current(port: int = 8765) -> str:
        """Get the name of the currently active project."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://localhost:{port}/current")
            return resp.text

    @mcp.tool()
    async def texwatch_switch(project: str, port: int = 8765) -> str:
        """Switch the active project by name."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:{port}/current",
                json={"project": project},
            )
            return resp.text

    @mcp.tool()
    async def texwatch_history(
        file: str,
        port: int = 8765,
        project: str | None = None,
    ) -> str:
        """Get previous versions of a source file (saved before each write). Returns snapshots newest-first."""
        base = _base_url(port, project)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/history/{file}")
            return resp.text

    @mcp.tool()
    async def texwatch_bibliography(port: int = 8765, project: str | None = None) -> str:
        """Get bibliography analysis: BibTeX entries, citations, uncited keys, and undefined keys."""
        return await _get("/bibliography", port, project)

    @mcp.tool()
    async def texwatch_environments(port: int = 8765, project: str | None = None) -> str:
        """Get LaTeX environments: theorems, equations, figures, tables, proofs, and more with labels and captions."""
        return await _get("/environments", port, project)

    @mcp.tool()
    async def texwatch_digest(port: int = 8765, project: str | None = None) -> str:
        """Get document metadata: class, title, author, date, abstract, packages, and custom commands."""
        return await _get("/digest", port, project)

    @mcp.tool()
    async def texwatch_dashboard(port: int = 8765, project: str | None = None) -> str:
        """Get unified paper dashboard: health, sections, issues, bibliography, changes, and environments in a single view."""
        return await _get("/dashboard", port, project)

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

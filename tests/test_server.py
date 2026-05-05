"""Smoke tests for the v0.4.0 server.

We don't run latexmk here; instead we instantiate the server, drive the
HTTP API directly, and check that comments + paper state plumbing works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from texwatch.config import Config
from texwatch.server import TexWatchServer


@pytest.fixture
def project(tmp_path: Path) -> Path:
    main = tmp_path / "paper.tex"
    main.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "\\label{sec:intro}\n"
        "Some prose with \\cite{ref1}.\n"
        "\\section{Methods}\n"
        "\\label{sec:methods}\n"
        "Some methods.\n"
        "\\end{document}\n"
    )
    return tmp_path


@pytest.fixture
async def client(project: Path):
    cfg = Config(main="paper.tex", config_path=project / ".texwatch.yaml")
    server = TexWatchServer(cfg)
    # Trigger a structure parse without compiling
    from texwatch.structure import parse_structure
    server.structure = parse_structure(project)
    test_server = TestServer(server.app)
    test_client = TestClient(test_server)
    await test_client.start_server()
    try:
        yield test_client, server
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_paper_endpoint_returns_structure(client):
    tc, _ = client
    resp = await tc.get("/paper")
    assert resp.status == 200
    data = await resp.json()
    titles = {s["title"] for s in data["sections"]}
    assert {"Introduction", "Methods"} <= titles
    assert {l["name"] for l in data["labels"]} == {"sec:intro", "sec:methods"}
    assert data["citations"][0]["key"] == "ref1"


@pytest.mark.asyncio
async def test_create_paper_anchor_comment(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={"anchor": {"kind": "paper"}, "text": "abstract is too long"},
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["status"] == "open"
    assert data["anchor"] == {"kind": "paper"}
    assert data["thread"][0]["text"] == "abstract is too long"
    cid = data["id"]

    resp = await tc.get("/comments")
    listed = await resp.json()
    assert len(listed["comments"]) == 1
    assert listed["comments"][0]["id"] == cid


@pytest.mark.asyncio
async def test_create_section_anchor_resolves_to_source(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {"kind": "section", "title": "Methods"},
            "text": "expand the methods section",
            "tags": ["structure"],
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["resolved_source"]["file"] == "paper.tex"
    assert data["resolved_source"]["line_start"] == 6  # \section{Methods} line


@pytest.mark.asyncio
async def test_create_source_range_anchor_captures_snippet(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 5, "line_end": 5},
            "text": "rephrase this citation",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["snippet"]
    assert "ref1" in data["snippet"]


@pytest.mark.asyncio
async def test_resolve_comment(client):
    tc, _ = client
    # Create
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]

    # Resolve
    resp = await tc.post(
        f"/comments/{cid}/resolve",
        json={"summary": "rewrote the abstract", "edits": ["paper.tex:1-10"]},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "resolved"
    assert data["thread"][-1]["edits"] == ["paper.tex:1-10"]


@pytest.mark.asyncio
async def test_dismiss_then_reopen(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]

    resp = await tc.post(f"/comments/{cid}/dismiss", json={"reason": "skip"})
    assert (await resp.json())["status"] == "dismissed"

    resp = await tc.post(f"/comments/{cid}/reopen", json={})
    assert (await resp.json())["status"] == "open"


@pytest.mark.asyncio
async def test_list_comments_filters(client):
    tc, _ = client
    a = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "open"})).json())["id"]
    b = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "to resolve"})).json())["id"]
    await tc.post(f"/comments/{b}/resolve", json={"summary": "done"})

    resp = await tc.get("/comments?status=open")
    open_ids = {c["id"] for c in (await resp.json())["comments"]}
    assert open_ids == {a}

    resp = await tc.get("/comments?status=resolved")
    resolved_ids = {c["id"] for c in (await resp.json())["comments"]}
    assert resolved_ids == {b}


@pytest.mark.asyncio
async def test_invalid_anchor_returns_400(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "bogus"}, "text": "x"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_missing_text_returns_400(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_get_unknown_comment_returns_404(client):
    tc, _ = client
    resp = await tc.get("/comments/c-doesntexist")
    assert resp.status == 404

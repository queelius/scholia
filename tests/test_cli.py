"""CLI smoke tests for the comment subcommands.

These run the CLI in-process via main([...]) against a tmp_path project.
Tests cover the surface that v0.4.0 actually adds: comment add/list/show/
resolve/dismiss/reopen/delete and the init scaffolder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from texwatch.cli import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    """Set up a tmp project directory with a minimal main.tex and chdir into it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "\\label{sec:intro}\n"
        "Some prose.\n"
        "\\section{Methods}\n"
        "More prose.\n"
        "\\end{document}\n"
    )
    return tmp_path


def test_init_creates_yaml(project_dir, capsys):
    rc = main(["init", "--main", "main.tex"])
    assert rc == 0
    assert (project_dir / ".texwatch.yaml").exists()


def test_init_refuses_to_overwrite_existing(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()  # discard
    rc = main(["init", "--main", "main.tex"])
    assert rc == 1


def test_init_force_overwrites(project_dir):
    main(["init", "--main", "main.tex"])
    rc = main(["init", "--main", "other.tex", "--force"])
    assert rc == 0


def test_comment_add_paper(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "abstract is too long", "--paper", "--tag", "prose"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[paper]" in out
    assert "abstract is too long" in out


def test_comment_add_section_resolves(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "expand methods", "--section", "Methods", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["anchor"]["kind"] == "section"
    assert data["resolved_source"]["file"] == "main.tex"
    assert data["resolved_source"]["line_start"] == 6  # \section{Methods}


def test_comment_add_section_warns_on_unknown(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "x", "--section", "Nonexistent"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" in err.lower()


def test_comment_add_source_captures_snippet(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main([
        "comment", "add", "rephrase",
        "--source", "main.tex:5-5",
        "--json",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["snippet"]
    assert "Some prose" in data["snippet"]


def test_comment_add_invalid_source_format(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "x", "--source", "garbage"])
    assert rc == 1


def test_comment_add_pdf_anchor(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "x", "--pdf", "3:10,20,100,40", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["anchor"]["kind"] == "pdf_region"
    assert data["anchor"]["page"] == 3


def test_comment_add_invalid_pdf_format(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "x", "--pdf", "garbage"])
    assert rc == 1


def test_comment_add_requires_anchor(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "add", "no anchor"])
    assert rc == 1


def test_comment_list_default_shows_open(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    main(["comment", "add", "open one", "--paper"])
    main(["comment", "add", "to be resolved", "--paper"])
    capsys.readouterr()
    rc = main(["comment", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "open one" in out
    assert "to be resolved" in out


def test_comment_list_empty(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "list"])
    assert rc == 0
    assert "no comments" in capsys.readouterr().out.lower()


def test_comment_list_json(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    main(["comment", "add", "x", "--paper", "--tag", "a"])
    capsys.readouterr()
    rc = main(["comment", "list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["tags"] == ["a"]


def test_comment_resolve_then_list_resolved(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    main(["comment", "add", "x", "--paper", "--json"])
    cid = json.loads(capsys.readouterr().out)["id"]

    rc = main([
        "comment", "resolve", cid, "rewrote that section",
        "--edit", "main.tex:5-10 -> :5-15",
    ])
    assert rc == 0
    capsys.readouterr()

    main(["comment", "list", "--status", "resolved", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["status"] == "resolved"
    assert data[0]["thread"][-1]["edits"] == ["main.tex:5-10 -> :5-15"]


def test_comment_dismiss_then_reopen(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    main(["comment", "add", "x", "--paper", "--json"])
    cid = json.loads(capsys.readouterr().out)["id"]

    main(["comment", "dismiss", cid, "skip"])
    capsys.readouterr()
    main(["comment", "list", "--status", "dismissed", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 1

    main(["comment", "reopen", cid])
    capsys.readouterr()
    main(["comment", "list", "--status", "open", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_comment_show(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    main(["comment", "add", "explain this", "--paper", "--json"])
    cid = json.loads(capsys.readouterr().out)["id"]

    rc = main(["comment", "show", cid])
    assert rc == 0
    out = capsys.readouterr().out
    assert cid in out
    assert "explain this" in out


def test_comment_show_unknown(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "show", "c-doesntexist"])
    assert rc == 1


def test_comment_delete(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    main(["comment", "add", "x", "--paper", "--json"])
    cid = json.loads(capsys.readouterr().out)["id"]

    rc = main(["comment", "delete", cid])
    assert rc == 0
    capsys.readouterr()  # discard "deleted ..." output

    main(["comment", "list", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_comment_delete_unknown(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "delete", "c-doesntexist"])
    assert rc == 1


def test_comment_resolve_unknown(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["comment", "resolve", "c-doesntexist", "summary"])
    assert rc == 1


def test_compile_command_returns_nonzero_when_no_compiler(project_dir, capsys, monkeypatch):
    """Without latexmk on PATH, compile should fail cleanly with exit code 1."""
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    monkeypatch.setenv("PATH", "")
    rc = main(["compile", "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is False

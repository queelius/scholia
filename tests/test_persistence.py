"""Tests for persistence module."""

import sqlite3
from pathlib import Path

import pytest

from texwatch.persistence import CompileStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / ".texwatch" / "history.db"
    return CompileStore(db_path)


class TestCompileStore:
    def test_creates_database(self, store):
        assert store.db_path.exists()

    def test_creates_tables(self, store):
        conn = sqlite3.connect(store.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "compiles" in tables
        assert "messages" in tables
        conn.close()

    def test_record_compile(self, store):
        compile_id = store.record_compile(
            project="test", success=True, duration_s=1.5,
            error_count=0, warning_count=2, word_count=1000, page_count=5,
            main_file="main.tex", messages=[],
        )
        assert compile_id == 1

    def test_record_compile_with_messages(self, store):
        compile_id = store.record_compile(
            project="test", success=False, duration_s=2.0,
            error_count=1, warning_count=0, word_count=None, page_count=None,
            main_file="main.tex",
            messages=[{"level": "error", "file": "main.tex", "line": 10, "message": "Undefined control sequence"}],
        )
        msgs = store.get_compile_messages(compile_id)
        assert len(msgs) == 1
        assert msgs[0]["level"] == "error"
        assert msgs[0]["message"] == "Undefined control sequence"


class TestQueryCompiles:
    def test_query_all(self, store):
        store.record_compile(project="a", success=True, duration_s=1.0,
            error_count=0, warning_count=0, word_count=100, page_count=1,
            main_file="main.tex", messages=[])
        store.record_compile(project="a", success=False, duration_s=2.0,
            error_count=1, warning_count=0, word_count=None, page_count=None,
            main_file="main.tex", messages=[])
        results = store.query_compiles()
        assert len(results) == 2

    def test_query_by_project(self, store):
        store.record_compile(project="a", success=True, duration_s=1.0,
            error_count=0, warning_count=0, word_count=100, page_count=1,
            main_file="main.tex", messages=[])
        store.record_compile(project="b", success=True, duration_s=1.0,
            error_count=0, warning_count=0, word_count=200, page_count=2,
            main_file="main.tex", messages=[])
        results = store.query_compiles(project="a")
        assert len(results) == 1
        assert results[0]["project"] == "a"

    def test_query_success_only(self, store):
        store.record_compile(project="a", success=True, duration_s=1.0,
            error_count=0, warning_count=0, word_count=100, page_count=1,
            main_file="main.tex", messages=[])
        store.record_compile(project="a", success=False, duration_s=2.0,
            error_count=1, warning_count=0, word_count=None, page_count=None,
            main_file="main.tex", messages=[])
        results = store.query_compiles(success=True)
        assert len(results) == 1
        assert results[0]["success"] == 1

    def test_query_with_limit(self, store):
        for i in range(10):
            store.record_compile(project="a", success=True, duration_s=1.0,
                error_count=0, warning_count=0, word_count=100, page_count=1,
                main_file="main.tex", messages=[])
        results = store.query_compiles(limit=3)
        assert len(results) == 3

    def test_query_ordered_newest_first(self, store):
        store.record_compile(project="a", success=True, duration_s=1.0,
            error_count=0, warning_count=0, word_count=100, page_count=1,
            main_file="main.tex", messages=[])
        store.record_compile(project="a", success=False, duration_s=2.0,
            error_count=1, warning_count=0, word_count=200, page_count=2,
            main_file="main.tex", messages=[])
        results = store.query_compiles()
        assert results[0]["id"] > results[1]["id"]

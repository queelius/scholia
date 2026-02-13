"""Tests for section-level change tracking."""

from texwatch.changes import ChangeLog, SectionDelta, compute_changes
from texwatch.structure import Section


# ---------------------------------------------------------------------------
# SectionDelta data class
# ---------------------------------------------------------------------------


class TestSectionDelta:
    """Tests for SectionDelta data class."""

    def test_create_delta(self):
        delta = SectionDelta(
            section_title="Introduction",
            section_file="intro.tex",
            section_line=5,
            lines_added=10,
            lines_removed=3,
            words_added=85,
            words_removed=20,
            is_dirty=True,
            diff_snippet="@@ -5,3 +5,12 @@\n+New paragraph",
            timestamp="2026-02-13T14:28:00Z",
        )
        assert delta.section_title == "Introduction"
        assert delta.section_file == "intro.tex"
        assert delta.section_line == 5
        assert delta.lines_added == 10
        assert delta.lines_removed == 3
        assert delta.words_added == 85
        assert delta.words_removed == 20
        assert delta.is_dirty is True
        assert delta.diff_snippet == "@@ -5,3 +5,12 @@\n+New paragraph"
        assert delta.timestamp == "2026-02-13T14:28:00Z"

    def test_delta_defaults_not_dirty(self):
        """A delta with zero adds/removes should still be representable."""
        delta = SectionDelta(
            section_title="Empty",
            section_file="main.tex",
            section_line=1,
            lines_added=0,
            lines_removed=0,
            words_added=0,
            words_removed=0,
            is_dirty=False,
            diff_snippet="",
            timestamp="",
        )
        assert delta.is_dirty is False
        assert delta.lines_added == 0


# ---------------------------------------------------------------------------
# ChangeLog ring buffer
# ---------------------------------------------------------------------------


class TestChangeLog:
    """Tests for ChangeLog ring buffer."""

    def test_create_empty(self):
        log = ChangeLog()
        assert log.deltas == []
        assert log.last_compiled_snapshots == {}

    def test_add_deltas(self):
        log = ChangeLog()
        delta = SectionDelta(
            section_title="Intro",
            section_file="main.tex",
            section_line=1,
            lines_added=5,
            lines_removed=0,
            words_added=30,
            words_removed=0,
            is_dirty=True,
            diff_snippet="",
            timestamp="2026-02-13T00:00:00Z",
        )
        log.record([delta])
        assert len(log.deltas) == 1
        assert log.deltas[0].section_title == "Intro"

    def test_record_multiple_batches(self):
        log = ChangeLog()
        for i in range(3):
            log.record([
                SectionDelta(
                    section_title=f"S{i}",
                    section_file="main.tex",
                    section_line=i,
                    lines_added=1,
                    lines_removed=0,
                    words_added=5,
                    words_removed=0,
                    is_dirty=True,
                    diff_snippet="",
                    timestamp=f"2026-02-13T00:0{i}:00Z",
                )
            ])
        assert len(log.deltas) == 3

    def test_ring_buffer_limit(self):
        log = ChangeLog(maxlen=3)
        for i in range(5):
            log.record([
                SectionDelta(
                    section_title=f"S{i}",
                    section_file="main.tex",
                    section_line=i,
                    lines_added=1,
                    lines_removed=0,
                    words_added=5,
                    words_removed=0,
                    is_dirty=True,
                    diff_snippet="",
                    timestamp=f"2026-02-13T00:0{i}:00Z",
                )
            ])
        assert len(log.deltas) == 3
        # Oldest entries (S0, S1) should have been evicted
        titles = [d.section_title for d in log.deltas]
        assert titles == ["S2", "S3", "S4"]

    def test_deltas_returns_list_copy(self):
        """The deltas property should return a list, not the internal deque."""
        log = ChangeLog()
        deltas = log.deltas
        assert isinstance(deltas, list)

    def test_record_batch_of_multiple(self):
        """Recording a batch with multiple deltas adds all of them."""
        log = ChangeLog()
        batch = [
            SectionDelta(
                section_title=f"S{i}",
                section_file="main.tex",
                section_line=i,
                lines_added=1,
                lines_removed=0,
                words_added=5,
                words_removed=0,
                is_dirty=True,
                diff_snippet="",
                timestamp="2026-02-13T00:00:00Z",
            )
            for i in range(4)
        ]
        log.record(batch)
        assert len(log.deltas) == 4


# ---------------------------------------------------------------------------
# compute_changes diffing function
# ---------------------------------------------------------------------------


class TestComputeChanges:
    """Tests for compute_changes diffing function."""

    def test_no_changes(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nHello world.\n"}
        new = {"main.tex": "\\section{Intro}\nHello world.\n"}
        deltas = compute_changes(sections, old, new)
        assert len(deltas) == 1
        assert deltas[0].is_dirty is False
        assert deltas[0].lines_added == 0
        assert deltas[0].lines_removed == 0

    def test_added_lines(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nLine one.\n"}
        new = {"main.tex": "\\section{Intro}\nLine one.\nLine two.\nLine three.\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].is_dirty is True
        assert deltas[0].lines_added == 2
        assert deltas[0].words_added >= 4

    def test_removed_lines(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nLine one.\nLine two.\n"}
        new = {"main.tex": "\\section{Intro}\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].is_dirty is True
        assert deltas[0].lines_removed == 2

    def test_multiple_sections(self):
        sections = [
            Section(level="section", title="Intro", file="main.tex", line=1),
            Section(level="section", title="Methods", file="main.tex", line=4),
        ]
        old = {
            "main.tex": (
                "\\section{Intro}\nOld intro.\n\n"
                "\\section{Methods}\nOld methods.\n"
            )
        }
        new = {
            "main.tex": (
                "\\section{Intro}\nNew intro.\n\n"
                "\\section{Methods}\nOld methods.\n"
            )
        }
        deltas = compute_changes(sections, old, new)
        intro = [d for d in deltas if d.section_title == "Intro"][0]
        methods = [d for d in deltas if d.section_title == "Methods"][0]
        assert intro.is_dirty is True
        assert methods.is_dirty is False

    def test_diff_snippet_present(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nOld text.\n"}
        new = {"main.tex": "\\section{Intro}\nNew text.\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].diff_snippet != ""

    def test_new_file_not_in_old(self):
        sections = [Section(level="section", title="Intro", file="new.tex", line=1)]
        deltas = compute_changes(sections, {}, {"new.tex": "\\section{Intro}\nNew.\n"})
        assert deltas[0].is_dirty is True
        assert deltas[0].lines_added > 0

    def test_empty_sections(self):
        assert compute_changes([], {}, {}) == []

    def test_timestamp_passed_through(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nOld.\n"}
        new = {"main.tex": "\\section{Intro}\nNew.\n"}
        deltas = compute_changes(sections, old, new, timestamp="2026-02-13T12:00:00Z")
        assert deltas[0].timestamp == "2026-02-13T12:00:00Z"

    def test_modified_lines_count(self):
        """Modified line should count as 1 removed + 1 added."""
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nOriginal text here.\n"}
        new = {"main.tex": "\\section{Intro}\nReplacement text here.\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].is_dirty is True
        assert deltas[0].lines_added >= 1
        assert deltas[0].lines_removed >= 1

    def test_section_file_and_line_in_delta(self):
        sections = [Section(level="section", title="Intro", file="ch1.tex", line=10)]
        old = {"ch1.tex": "\n" * 9 + "\\section{Intro}\nOld.\n"}
        new = {"ch1.tex": "\n" * 9 + "\\section{Intro}\nNew.\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].section_file == "ch1.tex"
        assert deltas[0].section_line == 10

    def test_word_count_on_additions(self):
        """Word counting should ignore LaTeX commands and short tokens."""
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\n"}
        new = {"main.tex": "\\section{Intro}\nThe quick brown fox jumps.\n"}
        deltas = compute_changes(sections, old, new)
        # "The", "quick", "brown", "fox", "jumps" = 5 words (all >= 2 chars)
        assert deltas[0].words_added >= 5

    def test_interleaved_files(self):
        """Sections from same file can be non-contiguous in section list."""
        sections = [
            Section(level="section", title="A", file="f1.tex", line=1),
            Section(level="section", title="B", file="f2.tex", line=1),
            Section(level="section", title="C", file="f1.tex", line=5),
        ]
        old = {
            "f1.tex": "\\section{A}\nOld A.\n\n\n\\section{C}\nOld C.\n",
            "f2.tex": "\\section{B}\nOld B.\n",
        }
        new = {
            "f1.tex": "\\section{A}\nNew A.\n\n\n\\section{C}\nOld C.\n",
            "f2.tex": "\\section{B}\nOld B.\n",
        }
        deltas = compute_changes(sections, old, new)
        assert deltas[0].is_dirty is True   # Section A changed
        assert deltas[1].is_dirty is False  # Section B unchanged
        assert deltas[2].is_dirty is False  # Section C unchanged


# ---------------------------------------------------------------------------
# Integration: ChangeLog + compute_changes cycle
# ---------------------------------------------------------------------------


class TestChangeLogIntegration:
    """Integration test for snapshot-diff-record cycle."""

    def test_snapshot_and_diff_cycle(self):
        log = ChangeLog()
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        new_contents = {"main.tex": "\\section{Intro}\nHello.\n"}

        # First compile: old snapshot is empty, so everything is new
        deltas = compute_changes(sections, log.last_compiled_snapshots, new_contents)
        log.record(deltas)
        log.last_compiled_snapshots = dict(new_contents)
        assert deltas[0].is_dirty is True

        # Second compile: same content -> not dirty
        deltas2 = compute_changes(
            sections, log.last_compiled_snapshots, new_contents
        )
        assert deltas2[0].is_dirty is False

    def test_two_edits_accumulate(self):
        log = ChangeLog()
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]

        v1 = {"main.tex": "\\section{Intro}\nFirst version.\n"}
        deltas1 = compute_changes(sections, log.last_compiled_snapshots, v1)
        log.record(deltas1)
        log.last_compiled_snapshots = dict(v1)

        v2 = {"main.tex": "\\section{Intro}\nSecond version with more words.\n"}
        deltas2 = compute_changes(sections, log.last_compiled_snapshots, v2)
        log.record(deltas2)
        log.last_compiled_snapshots = dict(v2)

        assert len(log.deltas) == 2
        assert log.deltas[0].is_dirty is True
        assert log.deltas[1].is_dirty is True


class TestChangeLogIntegrationWithSnapshots:
    def test_full_snapshot_cycle(self):
        """Test reading files and computing changes like the server would."""
        log = ChangeLog()
        sections = [
            Section(level="section", title="Intro", file="main.tex", line=1),
            Section(level="section", title="Methods", file="main.tex", line=4),
        ]
        v1 = {"main.tex": "\\section{Intro}\nOld.\n\n\\section{Methods}\nOld methods.\n"}
        deltas = compute_changes(sections, log.last_compiled_snapshots, v1)
        log.record(deltas)
        log.last_compiled_snapshots = dict(v1)

        v2 = {"main.tex": "\\section{Intro}\nNew intro added.\n\n\\section{Methods}\nOld methods.\n"}
        deltas2 = compute_changes(sections, log.last_compiled_snapshots, v2)
        log.record(deltas2)
        log.last_compiled_snapshots = dict(v2)

        dirty = [d for d in deltas2 if d.is_dirty]
        assert len(dirty) == 1
        assert dirty[0].section_title == "Intro"

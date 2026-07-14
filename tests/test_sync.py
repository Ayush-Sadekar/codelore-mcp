from codelore.sync import SYNC_LOG_HEADING, append_sync_log


def test_append_sync_log_creates_heading_when_absent():
    note = "# File: foo.py\n\nSome summary.\n"
    updated = append_sync_log(note, "abcdef1234567890", "no conflict — kept existing note")

    assert updated.startswith(note.rstrip("\n"))
    assert SYNC_LOG_HEADING in updated
    assert "commit abcdef12:" in updated
    assert "no conflict — kept existing note" in updated


def test_append_sync_log_inserts_under_existing_heading():
    note = (
        "# File: foo.py\n\n"
        f"{SYNC_LOG_HEADING}\n"
        "- 2026-01-01 — commit deadbeef: new file added\n"
    )
    updated = append_sync_log(note, "cafebabe00000000", "conflict — note updated")

    lines = updated.splitlines()
    heading_idx = lines.index(SYNC_LOG_HEADING)
    assert "commit cafebabe:" in lines[heading_idx + 1]
    assert "commit deadbeef:" in lines[heading_idx + 2]


def test_append_sync_log_preserves_trailing_newline_state():
    no_trailing = "# File: foo.py\n\nSummary"
    updated = append_sync_log(no_trailing, "1234567890abcdef", "new file added")
    assert not updated.endswith("\n\n\n")

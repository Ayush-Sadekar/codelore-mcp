import subprocess

import pytest

from codelore import llm


def test_judge_file_conflict_parses_yes(monkeypatch):
    monkeypatch.setattr(llm, "_call_claude", lambda prompt: "CONFLICT: yes\nREASON: behavior changed.")
    has_conflict, reason = llm.judge_file_conflict("f.py", "old", "new")
    assert has_conflict is True
    assert reason == "behavior changed."


def test_judge_file_conflict_parses_no(monkeypatch):
    monkeypatch.setattr(llm, "_call_claude", lambda prompt: "CONFLICT: no\nREASON: just a rename.")
    has_conflict, reason = llm.judge_file_conflict("f.py", "old", "new")
    assert has_conflict is False
    assert reason == "just a rename."


def test_judge_file_conflict_defaults_to_conflict_on_unparseable_response(monkeypatch):
    monkeypatch.setattr(llm, "_call_claude", lambda prompt: "not the expected format at all")
    has_conflict, reason = llm.judge_file_conflict("f.py", "old", "new")
    assert has_conflict is True
    assert "Could not parse" in reason


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["claude", "--print"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_call_claude_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(0, stdout="the summary\n"))
    assert llm._call_claude("prompt") == "the summary"


def test_call_claude_raises_after_persistent_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(1, stderr="rate limited"))
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="rate limited"):
        llm._call_claude("prompt")


def test_call_claude_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            return _completed(1, stderr="transient error")
        return _completed(0, stdout="ok\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)

    assert llm._call_claude("prompt") == "ok"
    assert calls["n"] == 2

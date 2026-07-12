"""
Per-file conflict-aware sync logic used by mcp_server.py's sync_vault tool.

For each modified file, sync_modified_file regenerates the file's summary the
same way initial ingestion does, then asks Claude to judge whether the new
note is a real, meaningful change vs the existing one. Only real conflicts
get written to the vault and re-indexed in ChromaDB; either way, the vault
note gets a Sync Log entry noting the commit that was checked.
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from pathlib import Path

import chromadb

from .llm import judge_file_conflict, summarize_files
from .nodes import FileNode
from .parsers import CHUNK_REGISTRY, REGISTRY
from .generate_questions import generate_questions_for_chunk, ChunkMetadata

SYNC_LOG_HEADING = "## 🔄 Sync Log"


@dataclass
class SyncFileResult:
    rel_path: str
    had_conflict: bool
    reason: str
    new_summary: str | None  # None when no conflict (old summary is kept)


def append_sync_log(note_content: str, commit_sha: str, verdict: str) -> str:
    """Append a dated Sync Log entry to a vault note, creating the section if needed."""
    date = datetime.date.today().isoformat()
    entry = f"- {date} — commit {commit_sha[:8]}: {verdict}"

    if SYNC_LOG_HEADING in note_content:
        lines = note_content.splitlines()
        idx = next(i for i, line in enumerate(lines) if line.strip() == SYNC_LOG_HEADING)
        lines.insert(idx + 1, entry)
        return "\n".join(lines) + ("\n" if note_content.endswith("\n") else "")

    separator = "" if note_content.endswith("\n") else "\n"
    return f"{note_content}{separator}\n{SYNC_LOG_HEADING}\n{entry}\n"


def _reindex_file_chunks(
    repo_root: Path,
    vault_root: Path,
    rel_path: str,
    abs_path: Path,
    collection: chromadb.Collection,
) -> int:
    """Generate fresh questions for every chunk in a file, then swap them into Chroma."""
    chunk_parser = CHUNK_REGISTRY.get(abs_path.suffix)
    if chunk_parser is None:
        return 0

    chunks = chunk_parser(abs_path, repo_root)
    markdown_path = str(vault_root / Path(rel_path).with_suffix(".md"))

    pending_chunks: list[tuple[list[str], dict]] = []
    for start_line, end_line, chunk_text in chunks:
        if not chunk_text.strip():
            continue
        meta = ChunkMetadata(
            file_path=str(abs_path),
            start_line=start_line,
            end_line=end_line,
            markdown_path=markdown_path,
        )
        result = generate_questions_for_chunk(chunk_text, meta)
        pending_chunks.append((result["questions"], result["metadata"]))

    # Only delete the old entries once the new ones are ready to insert, so a
    # failed regeneration doesn't leave the file with no indexed questions.
    collection.delete(where={"file_path": str(abs_path)})

    total = 0
    for questions, metadata in pending_chunks:
        if not questions:
            continue
        ids = [str(uuid.uuid4()) for _ in questions]
        collection.upsert(ids=ids, documents=questions, metadatas=[metadata] * len(questions))
        total += len(ids)
    return total


def sync_modified_file(
    repo_root: Path,
    vault_root: Path,
    collection: chromadb.Collection,
    rel_path: str,
    commit_sha: str,
    old_file_summary: str,
) -> SyncFileResult:
    """
    Regenerate rel_path's summary, judge it against the existing vault note,
    and only replace the note + reindex its questions on a real conflict.
    """
    abs_path = repo_root / rel_path
    code = abs_path.read_text(encoding="utf-8", errors="ignore")

    new_summary = summarize_files([(abs_path, code)], repo_root)[rel_path]

    parser = REGISTRY.get(abs_path.suffix)
    imports, _warnings = parser(abs_path, repo_root) if parser else ([], [])
    new_node = FileNode(path=Path(rel_path), name=abs_path.name, imports=imports, summary=new_summary)
    new_note = new_node.to_markdown()

    vault_md = vault_root / Path(rel_path).with_suffix(".md")
    old_note = vault_md.read_text(encoding="utf-8") if vault_md.exists() else ""

    has_conflict, reason = judge_file_conflict(rel_path, old_note, new_note)

    if not has_conflict:
        updated = append_sync_log(old_note, commit_sha, "no conflict — kept existing note")
        vault_md.parent.mkdir(parents=True, exist_ok=True)
        vault_md.write_text(updated, encoding="utf-8")
        return SyncFileResult(rel_path=rel_path, had_conflict=False, reason=reason, new_summary=None)

    _reindex_file_chunks(repo_root, vault_root, rel_path, abs_path, collection)

    updated = append_sync_log(new_note, commit_sha, "conflict — note updated")
    vault_md.parent.mkdir(parents=True, exist_ok=True)
    vault_md.write_text(updated, encoding="utf-8")
    return SyncFileResult(rel_path=rel_path, had_conflict=True, reason=reason, new_summary=new_summary)


def sync_new_file(
    repo_root: Path,
    vault_root: Path,
    collection: chromadb.Collection,
    rel_path: str,
    commit_sha: str,
) -> str:
    """
    First-time generation + indexing for a file that has no prior vault note.
    No conflict judging — there's nothing to compare against. Returns the new summary.
    """
    abs_path = repo_root / rel_path
    code = abs_path.read_text(encoding="utf-8", errors="ignore")

    new_summary = summarize_files([(abs_path, code)], repo_root)[rel_path]

    parser = REGISTRY.get(abs_path.suffix)
    imports, _warnings = parser(abs_path, repo_root) if parser else ([], [])
    new_node = FileNode(path=Path(rel_path), name=abs_path.name, imports=imports, summary=new_summary)
    new_note = new_node.to_markdown()

    _reindex_file_chunks(repo_root, vault_root, rel_path, abs_path, collection)

    updated = append_sync_log(new_note, commit_sha, "new file added")
    vault_md = vault_root / Path(rel_path).with_suffix(".md")
    vault_md.parent.mkdir(parents=True, exist_ok=True)
    vault_md.write_text(updated, encoding="utf-8")
    return new_summary

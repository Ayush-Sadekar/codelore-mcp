"""
Core retrieval functions for the codelore query system.

This module is intentionally not MCP-aware — it contains pure logic that
mcp_server.py calls. Keeping it separate means you can test or reuse these
functions outside the MCP context (e.g. in a CLI or a notebook).

There are four retrieval strategies here, each matching a query bucket:
  - search_chunks     → "how does X work?" (semantic / QuOTE search)
  - bfs_vault         → "explain the repo" (onboarding / graph traversal)
  - grep_todos        → "what's left to do?" (progress / task management)
  - git_file_log      → companion to grep_todos, shows recent commit history
"""
from __future__ import annotations

import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import chromadb

from ..embedding import EMBEDDING_MODEL_NAME, get_embedding_function

# Cosine distance above which a search_chunks match is considered unreliable.
# Empirically, unrelated text pairs under all-MiniLM-L6-v2 land around 0.7-1.0+;
# this is a starting point for callers to flag low-confidence results, not a
# hard cutoff enforced here (search_chunks always returns whatever Chroma finds).
DEFAULT_MAX_DISTANCE = 1.35


# ---------------------------------------------------------------------------
# Data types
#
# Each retrieval function returns one of these typed dataclasses rather than
# raw dicts so mcp_server.py can access fields by name without guessing keys.
# ---------------------------------------------------------------------------

@dataclass
class ChunkResult:
    # Absolute path to the source file on disk
    file_path: str
    # Line range of the chunk within that file (1-indexed, inclusive)
    start_line: int
    end_line: int
    # Absolute path to the vault .md summary for this file
    markdown_path: str
    # The developer question that matched the query (stored in ChromaDB as the document)
    question: str
    # Cosine distance — lower means more similar (0 = identical, 1 = unrelated)
    distance: float


@dataclass
class VaultNode:
    # How many hops from the start node (INDEX = 0, top-level dirs = 1, files = 2, …)
    depth: int
    # Path relative to vault_root, without the .md extension (e.g. "obsidian_init/ingest")
    node_path: str
    # Full markdown content of the vault note
    content: str


@dataclass
class TodoItem:
    file_path: str
    line_number: int
    text: str
    tag: str  # one of: TODO / FIXME / HACK


@dataclass
class CommitSummary:
    sha: str      # short commit hash
    message: str  # first line of the commit message


# ---------------------------------------------------------------------------
# ChromaDB search — the "QuOTE" system
#
# During ingestion, every code chunk (function, class, or whole file) was fed
# to Claude, which generated a list of natural-language developer questions that
# the chunk would answer. Those questions are stored as ChromaDB documents;
# the chunk's source location is stored as metadata on each document.
#
# At query time, the user's question is embedded and compared against all those
# stored questions using cosine similarity. The closest matches point back to
# the source chunks via their metadata.
#
# This means you're not searching the code directly — you're searching "what
# questions does this code answer?" which aligns much better with how developers
# actually look things up.
# ---------------------------------------------------------------------------

def _mismatch_error(chroma_path: str, detail: str) -> RuntimeError:
    return RuntimeError(
        f"codelore: the index at '{chroma_path}' can't be opened with the current "
        f"embedding model ('{EMBEDDING_MODEL_NAME}'): {detail} "
        "This is an index/embedding mismatch, NOT a repo-scope problem — "
        "repo_root is still correct. Fall back to direct Read/Grep on repo_root "
        "for this query, and consider re-running ingestion to rebuild the index "
        "with the current embedding model."
    )


def _get_collection(chroma_path: str) -> chromadb.Collection:
    # PersistentClient reads/writes the ChromaDB SQLite store at chroma_path.
    # get_or_create_collection is safe to call repeatedly — it's idempotent.
    # The cosine space and embedding function are set here and must match what
    # was used during ingestion (see generate_questions.py:get_or_create_collection).
    client = chromadb.PersistentClient(path=chroma_path)
    try:
        collection = client.get_or_create_collection(
            name="code_chunks",
            embedding_function=get_embedding_function(),
            metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL_NAME},
        )
    except ValueError as e:
        # Newer chromadb tracks the embedding function used at collection-creation
        # time and refuses get_or_create_collection outright on a conflict (e.g. an
        # index built before this project pinned an explicit embedding function).
        # Translate its generic message into one that tells the calling agent what
        # to actually do, instead of a bare ValueError with no next step.
        raise _mismatch_error(chroma_path, str(e)) from e

    # Belt-and-suspenders for chromadb versions without the native conflict check
    # above: collections created before embedding_model tracking existed won't
    # have this field — treat "unknown" as unverifiable rather than a hard
    # failure. Only raise when both sides are known and actually differ.
    stored_model = collection.metadata.get("embedding_model") if collection.metadata else None
    if stored_model and stored_model != EMBEDDING_MODEL_NAME:
        raise _mismatch_error(chroma_path, f"index was built with '{stored_model}'.")

    return collection


def search_chunks(query: str, chroma_path: str, n_results: int = 5) -> list[ChunkResult]:
    """
    Semantic search over the question-indexed code chunks.

    Returns up to n_results ChunkResult objects ranked by cosine similarity.
    Each result points to a specific line range in a source file and to its
    vault markdown summary.
    """
    collection = _get_collection(chroma_path)

    # Guard against querying an empty collection — ChromaDB raises if n > count.
    count = collection.count()
    if count == 0:
        return []
    n = min(n_results, count)

    # query_texts is a list so you could batch multiple queries, but we always
    # send one. The response shape is: {ids, distances, metadatas, documents},
    # each a list-of-lists (outer = one entry per query).
    results = collection.query(query_texts=[query], n_results=n)

    output: list[ChunkResult] = []
    for i, meta in enumerate(results["metadatas"][0]):
        output.append(ChunkResult(
            file_path=meta["file_path"],
            start_line=int(meta["start_line"]),
            end_line=int(meta["end_line"]),
            markdown_path=meta["markdown_path"],
            # documents[0][i] is the stored question string that matched
            question=results["documents"][0][i],
            distance=results["distances"][0][i],
        ))
    return output


# ---------------------------------------------------------------------------
# Vault BFS traversal — used for onboarding queries
#
# The vault is a directed graph of markdown files connected by wikilinks:
#   INDEX  →  DirectoryNodes  →  FileNodes
#
# Each node type encodes its edges differently in frontmatter:
#   type: index      → wikilinks are in the markdown body (parsed with regex)
#   type: directory  → edges listed under "children:" in frontmatter
#   type: file       → edges listed under "links:" in frontmatter (import graph)
#
# BFS from INDEX gives you a breadth-first discovery order: high-level
# directories first, then files, then their import dependencies. This is the
# natural "explain the codebase to me" traversal order.
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> dict[str, object]:
    """
    Extract YAML frontmatter fields from a vault markdown file.

    Handles two field shapes:
      scalar:  "key: value"
      list:    "key:\n  - item1\n  - item2"

    This is a hand-rolled parser (not PyYAML) to avoid an extra dependency.
    It only needs to handle the small subset of YAML that nodes.py produces.
    """
    if not content.startswith("---"):
        return {}
    # The closing "---" delimiter starts after the opening one (offset 3).
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end].strip()

    result: dict[str, object] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in fm_text.splitlines():
        if not line.strip():
            continue

        if line.startswith("  - "):
            # List item — append to the current list value.
            # line.strip() gives "- item", so [2:] strips the "- " prefix.
            if current_list is not None:
                current_list.append(line.strip()[2:])

        elif ": " in line or line.endswith(":"):
            # New key. Flush the previous list into result first.
            if current_list is not None and current_key:
                result[current_key] = current_list

            parts = line.split(": ", 1)
            # rstrip(":") handles "key:" (no value) where split gives ["key:"]
            current_key = parts[0].strip().rstrip(":")

            if len(parts) == 1 or parts[1].strip() == "":
                # No inline value — the next lines will be list items.
                current_list = []
                result[current_key] = current_list
            else:
                current_list = None
                result[current_key] = parts[1].strip()

    return result


def _links_from_node(content: str, node_type: str) -> list[str]:
    """
    Return the vault-relative paths that this node links to.

    The path format coming out of this function matches what bfs_vault uses
    to construct the .md file path: "{vault_root}/{path}.md".
    """
    fm = _parse_frontmatter(content)

    if node_type == "file":
        # "links" holds repo-relative import paths without extensions,
        # e.g. ["obsidian_init/nodes", "obsidian_init/parsers/__init__"]
        # These map directly to vault paths, so no transformation needed.
        raw = fm.get("links", [])
        return raw if isinstance(raw, list) else []

    if node_type == "directory":
        children = fm.get("children", [])
        if not isinstance(children, list):
            return []
        # "children" holds repo-relative paths WITH extensions (e.g. "src/foo.py").
        # Strip the extension so we can find the vault .md file for each child.
        result = []
        for child in children:
            p = Path(child)
            result.append(str(p.with_suffix("")) if p.suffix else child)
        return result

    if node_type == "index":
        # The INDEX node doesn't use frontmatter for its links — it uses
        # Obsidian wikilink syntax in the markdown body: [[path]].
        import re
        return re.findall(r"\[\[([^\]]+)\]\]", content)

    return []


def bfs_vault(vault_root: str, start_node: str = "INDEX", max_depth: int = 2) -> list[VaultNode]:
    """
    Breadth-first traversal of the vault graph starting from start_node.

    Returns a list of VaultNode in BFS order (shallowest first). Each node
    carries its depth, its vault-relative path, and the full markdown content
    so the caller doesn't need to re-read files.

    max_depth=2 is usually enough for an overview: INDEX → directories → files.
    Increase it to follow import links between files (depth 3+).
    """
    root = Path(vault_root)
    visited: set[str] = set()
    # Queue entries are (depth, node_path). Start at the INDEX node.
    queue: deque[tuple[int, str]] = deque([(0, start_node)])
    output: list[VaultNode] = []

    while queue:
        depth, node_path = queue.popleft()

        # Skip already-visited nodes to avoid cycles in the import graph.
        if node_path in visited:
            continue
        visited.add(node_path)

        md_file = root / f"{node_path}.md"
        if not md_file.exists():
            # A wikilink in the vault points to a file that doesn't exist —
            # silently skip rather than crashing (vault may be partial).
            continue

        content = md_file.read_text(encoding="utf-8")
        output.append(VaultNode(depth=depth, node_path=node_path, content=content))

        # Only enqueue children if we haven't hit the depth limit.
        if depth < max_depth:
            fm = _parse_frontmatter(content)
            node_type = str(fm.get("type", ""))
            for link in _links_from_node(content, node_type):
                if link not in visited:
                    queue.append((depth + 1, link))

    return output


# ---------------------------------------------------------------------------
# TODO / FIXME grep — used for progress/task-management queries
#
# Instead of maintaining a separate task list, we grep the source files
# directly. This keeps the "what's left to do" information colocated with
# the code it refers to, and it stays accurate as files change.
#
# The typical flow in mcp_server.py is:
#   1. search_chunks() to find files relevant to the user's question
#   2. grep_todos() on those files to surface open work items
#   3. git_file_log() on those files to show recent activity context
# ---------------------------------------------------------------------------

def grep_todos(repo_root: str, file_paths: list[str]) -> list[TodoItem]:
    """
    Grep for TODO/FIXME/HACK comments in the given files.

    file_paths can be absolute paths or paths relative to repo_root.
    Returns one TodoItem per matching line, with the tag (TODO/FIXME/HACK)
    extracted from the match.
    """
    items: list[TodoItem] = []
    for fp in file_paths:
        # Support both absolute paths (from ChromaDB metadata) and relative ones.
        full = Path(repo_root) / fp if not Path(fp).is_absolute() else Path(fp)
        if not full.exists():
            continue

        result = subprocess.run(
            ["grep", "-n", "-E", "TODO|FIXME|HACK", str(full)],
            capture_output=True, text=True,
        )
        # grep output format: "<line_number>:<matched line text>"
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            lineno_str, text = parts
            try:
                lineno = int(lineno_str)
            except ValueError:
                continue
            # Determine which tag triggered the match (first one found wins).
            tag = "TODO" if "TODO" in text else "FIXME" if "FIXME" in text else "HACK"
            items.append(TodoItem(file_path=fp, line_number=lineno, text=text.strip(), tag=tag))
    return items


# ---------------------------------------------------------------------------
# Git log — companion to grep_todos
# ---------------------------------------------------------------------------

def git_file_log(repo_root: str, file_path: str, n: int = 10) -> list[CommitSummary]:
    """
    Return the last n commits that touched file_path.

    Uses 'git -C <repo_root>' so this works regardless of the current
    working directory. If the repo has no git history (e.g. freshly cloned
    with --depth 1 and no commits), returns an empty list silently.
    """
    result = subprocess.run(
        ["git", "-C", repo_root, "log", "--oneline", f"-{n}", "--", file_path],
        capture_output=True, text=True,
    )
    commits: list[CommitSummary] = []
    # --oneline format: "<short-sha> <subject line>"
    for line in result.stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            commits.append(CommitSummary(sha=parts[0], message=parts[1]))
    return commits

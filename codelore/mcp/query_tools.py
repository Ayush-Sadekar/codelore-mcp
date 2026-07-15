"""Query tools — read from an already-ingested vault/ChromaDB index."""
import os
from pathlib import Path

from codelore.query.retrieval import (
    DEFAULT_MAX_DISTANCE,
    ChunkResult,
    VaultNode,
    bfs_vault,
    git_file_log,
    grep_todos,
    search_chunks,
)

from . import mcp
from .scope import _chroma_path, _repo_root, _vault_root


@mcp.tool()
def search_code(
    query: str,
    n_results: int = 5,
    vault_root: str = "",
    chroma_path: str = "",
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> str:
    """
    Search the codebase using semantic similarity.

    Use this for functional questions: how something works, where a function is
    defined, what a module does, or any vague/underspecified question about code
    behaviour. Searches a question-indexed vector database and returns the most
    relevant code chunks together with their vault summary.

    Each result is labeled Confidence: high or low based on whether its cosine
    distance is within max_distance (default 1.35 — tune it lower for stricter
    matching, higher to allow more speculative results through).

    FALLBACK — if this tool returns no results or all results are labeled low
    confidence, call the Obsidian MCP `search_simple` tool with the same query
    for a plain-text search across vault notes; do not grep the repo's source
    as a substitute for search — the vault is the source of truth for locating
    relevant code.

    Once you've located relevant code via a result's `file_path` (an absolute
    path into the target repo, not the vault), reading that file directly
    with Read/Grep is expected and normal when you need exact/current detail
    the vault summary doesn't cover — the vault summarizes, it doesn't
    replace the source.

    After finding results, use the Obsidian MCP `vault_read` tool to read full
    vault notes — try the vault-relative path first, and fall back to the
    absolute path if the Obsidian MCP rejects it (path format depends on how
    the Obsidian MCP server resolves paths against the vault root).

    GUARDRAIL: Never call the Obsidian MCP `vault_write` tool unless the user
    explicitly requests it by name.

    vault_root and chroma_path are optional — if omitted, the server uses the
    CODELORE_VAULT_ROOT and CODELORE_CHROMA_PATH environment variables. Pass
    them explicitly to query a different repo without reconfiguring the server.
    Resolves against the configured target repo (see server instructions) —
    not necessarily the current working directory.
    """
    results: list[ChunkResult] = search_chunks(query, _chroma_path(chroma_path), n_results)
    if not results:
        return "No results found. The ChromaDB index may be empty — run codelore ingestion first."

    vr = _vault_root(vault_root)
    lines: list[str] = []
    all_low_confidence = True
    for i, r in enumerate(results, 1):
        vault_md = Path(r.markdown_path)
        rel_path = vault_md.relative_to(vr) if vault_md.is_relative_to(vr) else vault_md
        is_confident = r.distance <= max_distance
        all_low_confidence = all_low_confidence and not is_confident
        confidence = "high" if is_confident else f"low (distance above {max_distance})"
        lines.append(
            f"### Result {i} (distance: {r.distance:.3f})\n"
            f"**Confidence:** {confidence}\n"
            f"**File:** `{r.file_path}` lines {r.start_line}–{r.end_line}\n"
            f"**Matched question:** {r.question}\n"
            f"**Vault note (relative):** `{rel_path}`\n"
            f"**Vault note (absolute):** `{vault_md}`\n"
            f"Use the Obsidian MCP `vault_read` tool on one of these paths for the summary."
        )

    header = (
        "_All results are below the confidence threshold — consider falling back "
        "to the Obsidian MCP `search_simple` tool with the same query._\n\n"
        if all_low_confidence else ""
    )
    return header + "\n\n".join(lines)


# DEPRECATED — not registered as an MCP tool. The Obsidian MCP `vault_read`
# tool is now the sole path for reading vault notes. Left here, unregistered,
# so it can be re-enabled with @mcp.tool() if the Obsidian MCP proves
# unreliable.
def read_vault_node(node_path: str, vault_root: str = "") -> str:
    """
    Reads a single node from the vault by its path. Pass paths like
    'INDEX', 'src/utils', or 'src/utils/helpers' (no .md extension needed).

    vault_root is optional — omit to use the CODELORE_VAULT_ROOT env var, or
    pass it explicitly to read from a different repo's vault.
    """
    vr = _vault_root(vault_root)
    clean = node_path.removesuffix(".md")
    md_file = Path(vr) / f"{clean}.md"
    if not md_file.exists():
        return f"Node not found: {md_file}. Check that the path is relative to the vault root."
    return md_file.read_text(encoding="utf-8")


@mcp.tool()
def explore_repo(max_depth: int = 2, vault_root: str = "") -> str:
    """
    Get a structured overview of the repo by traversing the vault graph.

    Use this for onboarding questions: 'explain the repo', 'where do I start',
    'give me an overview of the codebase', or 'what are the main components'.
    Starts from the vault INDEX and traverses breadth-first, returning summaries
    at increasing depth so you can understand the repo from the top down.

    To drill into a specific node after this overview, use the Obsidian MCP
    `vault_read` tool.

    GUARDRAIL: Never call the Obsidian MCP `vault_write` tool unless the user
    explicitly requests it by name.

    vault_root is optional — omit to use CODELORE_VAULT_ROOT, or pass it
    explicitly to explore a different repo's vault without reconfiguring.
    Resolves against the configured target repo (see server instructions) —
    not necessarily the current working directory.
    """
    nodes: list[VaultNode] = bfs_vault(_vault_root(vault_root), "INDEX", max_depth)
    if not nodes:
        return "Vault is empty or INDEX.md not found. Run codelore ingestion first."

    lines: list[str] = []
    for node in nodes:
        indent = "  " * node.depth
        summary = ""
        content = node.content
        fm_end = content.find("\n---", 3)
        body = content[fm_end + 4:] if fm_end != -1 else content
        body_lines = body.splitlines()

        for line in body_lines:
            stripped = line.strip()
            if stripped.startswith("##"):
                break
            if stripped and "placeholder" not in stripped and not stripped.startswith("<!--"):
                summary = stripped[:200]
                break

        if not summary:
            past_first_heading = False
            for line in body_lines:
                stripped = line.strip()
                if stripped.startswith("##") and not past_first_heading:
                    past_first_heading = True
                    continue
                if past_first_heading:
                    if stripped.startswith("##"):
                        break
                    if stripped and "placeholder" not in stripped and not stripped.startswith("<!--"):
                        summary = stripped[:200]
                        break

        lines.append(f"{indent}**{node.node_path}**" + (f" — {summary}" if summary else ""))

    return "## Repo Structure (BFS)\n\n" + "\n".join(lines)


@mcp.tool()
def find_todos(
    query: str,
    n_files: int = 5,
    vault_root: str = "",
    chroma_path: str = "",
    repo_root: str = "",
) -> str:
    """
    Find TODO/FIXME comments and recent git activity for relevant files.

    Use this for project progress or task management questions: 'what's left to
    do', 'what needs work in the parser', 'what files are incomplete', 'show me
    open tasks'. Searches for the most relevant files via semantic search, then
    scans them for TODO/FIXME/HACK comments and shows recent git commits.

    After identifying open tasks, you may use the Obsidian MCP `vault_append`
    tool to add notes or progress updates to the relevant vault files without
    overwriting existing content. Use `vault_read` (Obsidian MCP) to read the
    file before appending.

    GUARDRAIL: Never call the Obsidian MCP `vault_write` tool unless the user
    explicitly requests it by name.

    vault_root, chroma_path, and repo_root are optional — omit to use env vars,
    or pass them explicitly to query a different repo. Resolves against the
    configured target repo (see server instructions) — not necessarily the
    current working directory.
    """
    chroma = _chroma_path(chroma_path)
    repo = _repo_root(repo_root)

    results = search_chunks(query, chroma, n_results=n_files)
    if not results:
        return "No relevant files found in the index."

    seen: set[str] = set()
    file_paths: list[str] = []
    for r in results:
        fp = r.file_path
        if fp not in seen:
            seen.add(fp)
            file_paths.append(fp)

    output: list[str] = []
    for fp in file_paths:
        section: list[str] = [f"### `{fp}`"]

        todos = grep_todos(repo, [fp])
        if todos:
            section.append("**Open tasks:**")
            for t in todos:
                section.append(f"- Line {t.line_number} [{t.tag}]: {t.text}")
        else:
            section.append("_No TODO/FIXME/HACK comments found._")

        commits = git_file_log(repo, fp, n=5)
        if commits:
            section.append("\n**Recent commits:**")
            for c in commits:
                section.append(f"- `{c.sha}` {c.message}")
        else:
            section.append("_No git history found._")

        output.append("\n".join(section))

    return "\n\n".join(output)


@mcp.tool()
def read_guidelines(guidelines_path: str = "") -> str:
    """
    Return the project's architectural guidelines and coding conventions.

    Use this for questions about coding style, architectural patterns,
    conventions, how to structure new code, or what rules the project follows.

    GUARDRAIL: Never call the Obsidian MCP `vault_write` tool unless the user
    explicitly requests it by name.

    guidelines_path is optional — omit to use CODELORE_GUIDELINES_PATH, or
    pass a path directly to read any guidelines document without reconfiguring.
    """
    path = guidelines_path.strip() or os.environ.get("CODELORE_GUIDELINES_PATH", "").strip()
    if not path:
        return (
            "No guidelines document configured. "
            "Pass guidelines_path directly or set CODELORE_GUIDELINES_PATH."
        )
    p = Path(path)
    if not p.exists():
        return f"Guidelines file not found: {path}"
    return p.read_text(encoding="utf-8")


@mcp.tool()
def vault_append(
    query: str,
    vault_root: str = "",
    chroma_path: str = "",
) -> str:
    """
    Find the most relevant vault note for a user's annotation query and return
    its path so the Obsidian MCP `vault_append` tool can safely append to it.

    Use this when the user wants to add notes, progress updates, or observations
    to a vault file based on what they're currently doing — for example:
    'add a note about the parser edge case', 'mark this TODO as resolved',
    'append my findings on the auth module'.

    WORKFLOW:
      1. This tool resolves the target vault note path via semantic search.
      2. Use the Obsidian MCP `vault_read` tool to read the current content of
         that file before appending.
      3. Use the Obsidian MCP `vault_append` tool to add the new content to the
         end of the file. `vault_append` never overwrites existing content.

    GUARDRAIL: Use `vault_append` (Obsidian MCP) for all note additions.
    Never call the Obsidian MCP `vault_write` tool unless the user explicitly
    requests it by name — `vault_write` overwrites the entire file.

    Returns the resolved vault note path (both relative and absolute forms —
    try the relative one first, and fall back to the absolute one if the
    Obsidian MCP rejects it) so you can craft a contextual append. Use the
    Obsidian MCP `vault_read` tool on that path first to see the note's
    existing content before appending.

    vault_root and chroma_path are optional — omit to use env vars.
    """
    results = search_chunks(query, _chroma_path(chroma_path), n_results=1)
    if not results:
        return (
            "No relevant vault note found for that query. "
            "Check that the repo has been ingested, then use the Obsidian MCP "
            "`vault_append` tool with a manual path if you know the target file."
        )

    vr = _vault_root(vault_root)
    vault_md = Path(results[0].markdown_path)
    rel_path = vault_md.relative_to(vr) if vault_md.is_relative_to(vr) else vault_md
    return (
        f"**Target vault note (relative):** `{rel_path}`\n"
        f"**Target vault note (absolute):** `{vault_md}`\n\n"
        f"Next steps:\n"
        f"1. Use Obsidian MCP `vault_read` with one of the paths above to see the note's existing content.\n"
        f"2. Use Obsidian MCP `vault_append` with the same path to add your content.\n"
        f"   `vault_append` is safe — it appends only and never overwrites."
    )

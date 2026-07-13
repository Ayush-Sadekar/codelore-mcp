"""
codelore MCP server — exposes repo ingestion and query tools to Claude Code.

HOW THIS FILE IS ORGANIZED
  1. Config helpers   — read env vars OR accept per-call overrides
  2. Query tools      — read from an already-ingested vault/ChromaDB index
  3. Ingestion tools  — run the full pipeline to create a vault/index from scratch

ENV VARS (used when per-call paths are not provided):
  CODELORE_VAULT_ROOT      path to the *_vault/ directory
  CODELORE_CHROMA_PATH     path to the *_chroma/ directory
  CODELORE_REPO_ROOT       path to the source repo (used by grep and git log)
  CODELORE_GUIDELINES_PATH path to an architectural guidelines doc (optional)

PER-CALL OVERRIDES
  Every query tool accepts optional vault_root / chroma_path / repo_root params.
  These override the env vars, so a single server instance can be pointed at
  different repos on a per-call basis — no need to restart or reconfigure.

HOW TOOLS ARE ROUTED
  Claude decides which tool to call based on the tool's docstring. There is no
  explicit routing layer — the docstrings describe the query type each tool
  handles so Claude self-routes correctly.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import chromadb
from mcp.server.fastmcp import FastMCP
from codelore.query.retrieval import (
    ChunkResult,
    VaultNode,
    bfs_vault,
    git_file_log,
    grep_todos,
    search_chunks,
)

SERVER_INSTRUCTIONS = """\
codelore answers questions about a specific TARGET repo — configured via
CODELORE_VAULT_ROOT / CODELORE_CHROMA_PATH / CODELORE_REPO_ROOT env vars or
per-call overrides. That target repo is NOT necessarily the current working
directory of this session.

For codebase questions (architecture, "how does X work", "where is Y
defined", open TODOs), prefer search_code / explore_repo / find_todos over
ad hoc Read/Grep/Bash on the working directory — those tools query the
correct target repo's vault and index directly. If a tool call fails because
scope looks misconfigured (e.g. it resolves inside codelore's own source),
do not silently fall back to grepping cwd — ask the user which repo they
mean, or call get_active_scope to see current resolution state.

Vault notes are AI-generated summaries and can lag behind the current code
(sync_vault intentionally leaves a note unchanged when a diff is judged
non-behavioral). If the vault doesn't have enough detail to answer precisely
— exact signatures, current line numbers, anything the summary doesn't cover
— use Read/Grep/Bash directly on the repo that corresponds to the vault
(repo_root, as resolved by get_active_scope or CODELORE_REPO_ROOT, or the
absolute file_path a search_code result already gives you) rather than
guessing from the summary. This is different from grepping the working
directory blind: it's targeted reading of the specific repo codelore is
scoped to, once you know where that repo is.
"""

mcp = FastMCP("codelore", instructions=SERVER_INSTRUCTIONS)


# ---------------------------------------------------------------------------
# Config helpers
#
# Each helper accepts an optional override string. If the caller passes a
# non-empty string, it's used directly. Otherwise we fall back to the env var.
# This lets the query tools accept per-call paths while still working with
# the env-var convention when no override is provided.
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"codelore: {name} is not set. Pass it directly to the tool or "
            "configure it as an environment variable in your MCP client config."
        )
    return val


_CODELORE_PKG_DIR = Path(__file__).resolve().parent


def _check_not_self_scope(label: str, resolved: str) -> str:
    p = Path(resolved).resolve()
    if p == _CODELORE_PKG_DIR or _CODELORE_PKG_DIR in p.parents:
        raise RuntimeError(
            f"codelore: resolved {label} ('{resolved}') points inside codelore's "
            "own source tree. This usually means CODELORE_REPO_ROOT/VAULT_ROOT/"
            "CHROMA_PATH are misconfigured or unset for the repo you actually mean. "
            "Ask the user to confirm which repo to target, or pass an explicit "
            "override — do not fall back to reading the current working directory "
            "directly. If you really do mean to introspect codelore itself, pass "
            "the override explicitly to confirm intent."
        )
    return resolved


def _vault_root(override: str = "") -> str:
    return _check_not_self_scope("vault_root", override.strip() or _require_env("CODELORE_VAULT_ROOT"))


def _chroma_path(override: str = "") -> str:
    return _check_not_self_scope("chroma_path", override.strip() or _require_env("CODELORE_CHROMA_PATH"))


def _repo_root(override: str = "") -> str:
    return _check_not_self_scope("repo_root", override.strip() or _require_env("CODELORE_REPO_ROOT"))


@mcp.tool()
def get_active_scope(vault_root: str = "", chroma_path: str = "", repo_root: str = "") -> str:
    """
    Report which repo this codelore server is currently scoped to — useful to
    sanity-check before a multi-step task, or to debug a misconfigured
    .mcp.json. Not required before calling other tools: they already refuse
    to resolve to codelore's own source on their own.
    """
    rows: list[str] = []

    def _check(label: str, resolver, override: str) -> None:
        try:
            resolved = resolver(override)
        except RuntimeError as e:
            status = "SELF-SCOPE" if "own source tree" in str(e) else "MISSING"
            rows.append(f"| `{label}` | _(unresolved)_ | {status}: {e} |")
            return
        rows.append(f"| `{label}` | `{resolved}` | OK |")

    _check("vault_root", _vault_root, vault_root)
    _check("chroma_path", _chroma_path, chroma_path)
    _check("repo_root", _repo_root, repo_root)

    return (
        "## codelore active scope\n\n"
        "| Field | Resolved value | Status |\n"
        "|---|---|---|\n" + "\n".join(rows)
    )


# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_code(
    query: str,
    n_results: int = 5,
    vault_root: str = "",
    chroma_path: str = "",
) -> str:
    """
    Search the codebase using semantic similarity.

    Use this for functional questions: how something works, where a function is
    defined, what a module does, or any vague/underspecified question about code
    behaviour. Searches a question-indexed vector database and returns the most
    relevant code chunks together with their vault summary.

    FALLBACK — if this tool returns no results or low-confidence matches, call
    the Obsidian MCP `search_simple` tool with the same query for a plain-text
    search across vault notes; do not grep the repo's source as a substitute
    for search — the vault is the source of truth for locating relevant code.

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
    for i, r in enumerate(results, 1):
        vault_md = Path(r.markdown_path)
        rel_path = vault_md.relative_to(vr) if vault_md.is_relative_to(vr) else vault_md
        lines.append(
            f"### Result {i} (distance: {r.distance:.3f})\n"
            f"**File:** `{r.file_path}` lines {r.start_line}–{r.end_line}\n"
            f"**Matched question:** {r.question}\n"
            f"**Vault note (relative):** `{rel_path}`\n"
            f"**Vault note (absolute):** `{vault_md}`\n"
            f"Use the Obsidian MCP `vault_read` tool on one of these paths for the summary."
        )
    return "\n\n".join(lines)


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


# ---------------------------------------------------------------------------
# Ingestion helpers (private)
# ---------------------------------------------------------------------------

_GITHUB_PREFIXES = ("https://github.com/", "http://github.com/", "git@github.com:")


def _is_github_url(arg: str) -> bool:
    return any(arg.startswith(p) for p in _GITHUB_PREFIXES)


def _repo_name_from_url(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    return name.removesuffix(".git")


def _clone_repo(url: str, dest: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr.strip()}")


def _check_claude_cli() -> None:
    if shutil.which("claude") is None:
        raise RuntimeError(
            "The 'claude' CLI is not on PATH. Install Claude Code to use ingestion tools."
        )


# ---------------------------------------------------------------------------
# Ingestion tools
# ---------------------------------------------------------------------------

@mcp.tool()
def estimate_cost(repo_path: str) -> str:
    """
    Estimate how many Claude CLI calls ingesting a repo will require.

    Use this BEFORE running ingest_repo on a large codebase to understand the
    scope. Reports file counts by language and the total number of 'claude --print'
    subprocess calls that will be made.

    Does not call Claude or modify anything — safe to run at any time.
    """
    from codelore.explain import collect_files
    from codelore.parsers.python import parse_chunks

    root = Path(repo_path).resolve()
    if not root.is_dir():
        return f"Error: {repo_path} is not a directory."

    files = collect_files(root)
    if not files:
        return "No supported code files found in this repo."

    lang_map: dict[str, int] = {}
    for f in files:
        ext = f.suffix
        lang_map[ext] = lang_map.get(ext, 0) + 1

    total_chunks = 0
    for f in files:
        if f.suffix == ".py":
            try:
                chunks = parse_chunks(f, root)
                total_chunks += max(1, len(chunks))
            except Exception:
                total_chunks += 1
        else:
            total_chunks += 1

    dirs: set[str] = set()
    for f in files:
        rel = f.relative_to(root)
        if rel.parent != Path("."):
            dirs.add(rel.parent.as_posix())

    n_files = len(files)
    n_dirs = len(dirs)
    total_calls = n_files + n_dirs + total_chunks

    lang_lines = "\n".join(
        f"  {ext or '(no ext)'}: {count} file{'s' if count != 1 else ''}"
        for ext, count in sorted(lang_map.items(), key=lambda x: -x[1])
    )

    return (
        f"## Cost Estimate for `{root.name}`\n\n"
        f"**Files to summarize:** {n_files} ({n_dirs} directories)\n"
        f"**Code chunks to index:** {total_chunks}\n"
        f"**Total `claude --print` calls:** ~{total_calls}\n\n"
        f"**By language:**\n{lang_lines}\n\n"
        f"_Tip: run `ingest_repo` with confidence, or pass an existing "
        f"explanations.json to `rebuild_vault` to skip LLM calls entirely._"
    )


@mcp.tool()
def ingest_repo(
    repo_path_or_url: str,
    vault_output_path: str = "",
    extra_frontmatter_json: str = "",
) -> str:
    """
    Ingest a code repository into codelore — generates the vault and search index.

    Accepts either a local directory path or a GitHub URL
    (https://github.com/owner/repo). Runs the full pipeline:
    1. Generates AI summaries for every file and directory via Claude CLI
    2. Writes an Obsidian-compatible vault of markdown notes
    3. Indexes code chunks as developer questions into ChromaDB

    extra_frontmatter_json — optional JSON object of extra fields to add to every
    vault note's frontmatter, e.g. '{"project": "myapp", "status": "draft",
    "tags": ["backend", "python"]}'. Strings, numbers, booleans, and flat lists
    are all supported. These fields are merged after the built-in fields.

    After ingestion, the tool prints the vault and chroma paths. Pass these as
    vault_root and chroma_path to the query tools, or set them as env vars.

    WARNING: calls 'claude --print' once per file + directory + chunk.
    Run estimate_cost first on large repos.
    """
    _check_claude_cli()

    from codelore.explain import collect_files, explain_repo, index_repo_questions
    from codelore.ingest import build_tree, write_vault
    from codelore.generate_questions import get_or_create_collection

    tmp_dir = None
    try:
        if _is_github_url(repo_path_or_url):
            tmp_dir = tempfile.mkdtemp(prefix="codelore_")
            repo_root = Path(tmp_dir) / _repo_name_from_url(repo_path_or_url)
            _clone_repo(repo_path_or_url, repo_root)
            default_name = _repo_name_from_url(repo_path_or_url)
        else:
            repo_root = Path(repo_path_or_url).resolve()
            if not repo_root.is_dir():
                return f"Error: {repo_path_or_url} is not a directory."
            default_name = repo_root.name

        if vault_output_path:
            vault_root = Path(vault_output_path).resolve()
        else:
            vault_root = repo_root.parent / f"{default_name}_vault"

        chroma_path = vault_root.parent / f"{default_name}_chroma"

        all_files = collect_files(repo_root)
        if not all_files:
            return "No supported code files found."
        file_code_pairs = [
            (f, f.read_text(encoding="utf-8", errors="ignore")) for f in all_files
        ]
        file_code_pairs = [(f, c) for f, c in file_code_pairs if c.strip()]

        file_summaries, dir_summaries = explain_repo(repo_root)

        # Capture the current HEAD SHA so sync_vault can later diff against it.
        sha_result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        head_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None

        explanations_out = vault_root.parent / f"{default_name}_explanations.json"
        vault_root.parent.mkdir(parents=True, exist_ok=True)
        explanations_out.write_text(
            json.dumps(
                {"repo_head_sha": head_sha, "files": file_summaries, "directories": dir_summaries},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        extra_fm: dict = {}
        if extra_frontmatter_json.strip():
            try:
                extra_fm = json.loads(extra_frontmatter_json)
                if not isinstance(extra_fm, dict):
                    return "Error: extra_frontmatter_json must be a JSON object, e.g. '{\"project\": \"myapp\"}'"
            except json.JSONDecodeError as e:
                return f"Error parsing extra_frontmatter_json: {e}"

        index, all_nodes, warnings = build_tree(repo_root, file_summaries, dir_summaries, extra_fm or None)
        write_vault(index, all_nodes, warnings, vault_root, extra_fm or None)

        chroma_client = chromadb.PersistentClient(path=str(chroma_path))
        collection = get_or_create_collection(chroma_client)
        n_questions = index_repo_questions(repo_root, vault_root, file_code_pairs, collection)

        return (
            f"## Ingestion complete\n\n"
            f"**Repo:** `{repo_root}`\n"
            f"**Files processed:** {len(file_code_pairs)}\n"
            f"**Vault:** `{vault_root}` ({1 + len(all_nodes)} notes)\n"
            f"**ChromaDB:** `{chroma_path}` ({n_questions} questions indexed)\n"
            f"**Explanations saved:** `{explanations_out}`\n"
            + (f"**Warnings:** {len(warnings)}\n" if warnings else "")
            + f"\nTo query this repo, pass these paths to the query tools:\n"
            f"  vault_root={vault_root}\n"
            f"  chroma_path={chroma_path}\n"
            f"  repo_root={repo_root}\n\n"
            f"Or set them as env vars: CODELORE_VAULT_ROOT, CODELORE_CHROMA_PATH, CODELORE_REPO_ROOT"
        )
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

# not sure if this is like a 'lint' func where only new files are put thru the claude --print stuff
@mcp.tool()
def rebuild_vault(
    repo_path: str,
    explanations_json_path: str,
    vault_output_path: str = "",
    extra_frontmatter_json: str = "",
) -> str:
    """
    Rebuild the vault and search index from an existing explanations file.

    Use this to re-run vault generation WITHOUT making any Claude CLI calls for
    file summaries. Useful when you want to re-index after code changes but
    already have summaries, or to iterate on vault structure without LLM cost.

    extra_frontmatter_json — optional JSON object of extra fields to add to every
    vault note's frontmatter, e.g. '{"project": "myapp", "status": "draft",
    "tags": ["backend", "python"]}'. Strings, numbers, booleans, and flat lists
    are all supported.

    Note: ChromaDB question generation still calls Claude once per chunk —
    only the file/directory summaries are skipped (they're loaded from JSON).

    The explanations.json is saved automatically by ingest_repo.
    """
    from codelore.explain import collect_files, index_repo_questions
    from codelore.ingest import build_tree, write_vault
    from codelore.generate_questions import get_or_create_collection

    repo_root = Path(repo_path).resolve()
    if not repo_root.is_dir():
        return f"Error: {repo_path} is not a directory."

    explanations_path = Path(explanations_json_path).resolve()
    if not explanations_path.exists():
        return f"Error: explanations file not found: {explanations_json_path}"

    stored = json.loads(explanations_path.read_text(encoding="utf-8"))
    if "files" in stored and "directories" in stored:
        file_summaries = stored["files"]
        dir_summaries = stored["directories"]
    else:
        file_summaries = stored
        dir_summaries = {}

    if vault_output_path:
        vault_root = Path(vault_output_path).resolve()
    else:
        vault_root = repo_root.parent / f"{repo_root.name}_vault"

    chroma_path = vault_root.parent / f"{repo_root.name}_chroma"

    extra_fm: dict = {}
    if extra_frontmatter_json.strip():
        try:
            extra_fm = json.loads(extra_frontmatter_json)
            if not isinstance(extra_fm, dict):
                return "Error: extra_frontmatter_json must be a JSON object, e.g. '{\"project\": \"myapp\"}'"
        except json.JSONDecodeError as e:
            return f"Error parsing extra_frontmatter_json: {e}"

    index, all_nodes, warnings = build_tree(repo_root, file_summaries, dir_summaries, extra_fm or None)
    write_vault(index, all_nodes, warnings, vault_root, extra_fm or None)

    all_files = collect_files(repo_root)
    file_code_pairs = [
        (f, f.read_text(encoding="utf-8", errors="ignore")) for f in all_files
    ]
    file_code_pairs = [(f, c) for f, c in file_code_pairs if c.strip()]

    chroma_client = chromadb.PersistentClient(path=str(chroma_path))
    collection = get_or_create_collection(chroma_client)
    n_questions = index_repo_questions(repo_root, vault_root, file_code_pairs, collection)

    return (
        f"## Vault rebuilt\n\n"
        f"**Repo:** `{repo_root}`\n"
        f"**Vault:** `{vault_root}` ({1 + len(all_nodes)} notes)\n"
        f"**ChromaDB:** `{chroma_path}` ({n_questions} questions indexed)\n"
        + (f"**Warnings:** {len(warnings)}\n" if warnings else "")
    )


@mcp.tool()
def sync_vault(
    repo_path: str,
    explanations_json_path: str,
    dry_run: bool = True,
    vault_root: str = "",
    chroma_path: str = "",
) -> str:
    """
    Detect and apply repo changes since the last ingest.

    Use this to keep the vault and search index in sync after code changes
    without re-processing the entire repo. Uses git diff against the commit
    SHA saved during the last ingest_repo run.

    dry_run=True (default): reports changed, new, and deleted files — no changes made.
    dry_run=False: for each modified file, regenerates its summary and asks Claude
    whether it's a REAL conflict vs. the existing vault note (not just phrasing/
    comment/formatting drift). Only real conflicts replace the note and reindex
    that file's ChromaDB questions — everything else is left as-is. Either way,
    the vault note gets a Sync Log entry noting the commit that was checked.
    New files are ingested for the first time; deleted files are removed.

    Always run with dry_run=True first to review the change set, then call
    again with dry_run=False to apply. Requires the repo to be a git repository.
    """
    from codelore.generate_questions import get_or_create_collection
    from codelore.parsers import REGISTRY
    from codelore.sync import sync_modified_file, sync_new_file

    repo_root = Path(repo_path).resolve()
    if not repo_root.is_dir():
        return f"Error: {repo_path} is not a directory."

    explanations_path = Path(explanations_json_path).resolve()
    if not explanations_path.exists():
        return f"Error: explanations file not found: {explanations_json_path}"

    stored = json.loads(explanations_path.read_text(encoding="utf-8"))
    saved_sha = stored.get("repo_head_sha")
    if not saved_sha:
        return (
            "Error: no repo_head_sha found in explanations JSON. "
            "Re-run ingest_repo to save the commit SHA, then sync_vault will work."
        )
    file_summaries: dict[str, str] = stored.get("files", stored)
    dir_summaries: dict[str, str] = stored.get("directories", {})

    vr = Path(_vault_root(vault_root))
    cp = _chroma_path(chroma_path)

    # --- Compute change set via git diff ---
    def _git(*args: str) -> tuple[int, str]:
        r = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    rc, current_sha = _git("rev-parse", "HEAD")
    if rc != 0:
        return "Error: repo is not a git repository or has no commits."

    _, changed_raw = _git("diff", f"{saved_sha}..HEAD", "--name-only", "--diff-filter=ACM")
    _, deleted_raw = _git("diff", f"{saved_sha}..HEAD", "--name-only", "--diff-filter=D")

    # Filter to only extensions codelore knows about.
    def _supported(rel_path: str) -> bool:
        return Path(rel_path).suffix in REGISTRY

    changed_paths = [p for p in changed_raw.splitlines() if p and _supported(p)]
    deleted_paths = [p for p in deleted_raw.splitlines() if p and _supported(p)]

    # New files = changed but not previously in explanations JSON.
    new_paths = [p for p in changed_paths if p not in file_summaries]
    modified_paths = [p for p in changed_paths if p in file_summaries]

    if dry_run:
        lines = [
            f"## Vault Sync Report\n",
            f"**Repo:** `{repo_root}` (HEAD: `{current_sha[:8]}`)",
            f"**Last ingested at:** `{saved_sha[:8]}`\n",
        ]
        if modified_paths:
            lines.append(f"**Modified ({len(modified_paths)})** — will be re-summarized and re-indexed:")
            lines.extend(f"  - {p}" for p in modified_paths)
        if new_paths:
            lines.append(f"\n**New ({len(new_paths)})** — will be ingested for the first time:")
            lines.extend(f"  - {p}" for p in new_paths)
        if deleted_paths:
            lines.append(f"\n**Deleted ({len(deleted_paths)})** — will be removed from vault and ChromaDB:")
            lines.extend(f"  - {p}" for p in deleted_paths)
        if not (modified_paths or new_paths or deleted_paths):
            lines.append("**No supported files changed.** Vault is up to date.")
            return "\n".join(lines)

        total_calls = len(modified_paths) + len(new_paths)
        lines.append(f"\n_Run with `dry_run=False` to apply (~{total_calls} `claude --print` calls)._")
        return "\n".join(lines)

    # --- Apply changes ---
    if not (modified_paths or new_paths or deleted_paths):
        return "Vault is already up to date — no supported files changed."

    _check_claude_cli()
    chroma_client = chromadb.PersistentClient(path=str(cp))
    collection = get_or_create_collection(chroma_client)

    # Modified files: regenerate the summary, judge it against the existing
    # note, and only replace the note + reindex questions on a real conflict.
    n_conflicts = 0
    n_no_conflict = 0
    for rel in modified_paths:
        abs_path = repo_root / rel
        if not abs_path.exists():
            continue
        result = sync_modified_file(repo_root, vr, collection, rel, current_sha, file_summaries.get(rel, ""))
        if result.had_conflict:
            n_conflicts += 1
            file_summaries[rel] = result.new_summary
        else:
            n_no_conflict += 1

    # New files: first-time generation + indexing, no conflict to judge.
    for rel in new_paths:
        abs_path = repo_root / rel
        if not abs_path.exists():
            continue
        file_summaries[rel] = sync_new_file(repo_root, vr, collection, rel, current_sha)

    # Remove deleted files from vault and ChromaDB.
    for rel in deleted_paths:
        abs_path = repo_root / rel
        vault_md = vr / f"{Path(rel).with_suffix('')}.md"
        if vault_md.exists():
            vault_md.unlink()
        collection.delete(where={"file_path": str(abs_path)})
        file_summaries.pop(rel, None)

    # Persist updated explanations JSON with new HEAD SHA.
    explanations_path.write_text(
        json.dumps(
            {"repo_head_sha": current_sha, "files": file_summaries, "directories": dir_summaries},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return (
        f"## Sync complete\n\n"
        f"**Repo:** `{repo_root}`\n"
        f"**Modified files with real conflicts (note + questions updated):** {n_conflicts}\n"
        f"**Modified files with no conflict (note left as-is, commit logged):** {n_no_conflict}\n"
        f"**New files ingested:** {len(new_paths)}\n"
        f"**Deleted files removed:** {len(deleted_paths)}\n"
        f"**SHA advanced:** `{saved_sha[:8]}` → `{current_sha[:8]}`\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()

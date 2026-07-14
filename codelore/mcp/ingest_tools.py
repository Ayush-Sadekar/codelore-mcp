"""Ingestion tools — run the full pipeline to create a vault/index from scratch."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import chromadb

from . import mcp
from .scope import _chroma_path, _vault_root

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


def _is_supported_ext(rel_path: str) -> bool:
    """True if codelore has a parser registered for this file's extension."""
    from codelore.parsers import REGISTRY

    return Path(rel_path).suffix in REGISTRY


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

        try:
            file_summaries, dir_summaries = explain_repo(repo_root)
        except RuntimeError as e:
            return f"Error: {e}"

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
        try:
            n_questions = index_repo_questions(repo_root, vault_root, file_code_pairs, collection)
        except RuntimeError as e:
            return (
                f"Error generating search-index questions: {e}\n\n"
                f"The vault itself was written successfully to `{vault_root}` "
                f"(summaries saved to `{explanations_out}`) — re-run `rebuild_vault` "
                f"with that explanations file to retry indexing without re-summarizing."
            )

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
    try:
        n_questions = index_repo_questions(repo_root, vault_root, file_code_pairs, collection)
    except RuntimeError as e:
        return (
            f"Error generating search-index questions: {e}\n\n"
            f"The vault itself was written successfully to `{vault_root}` — "
            f"re-run `rebuild_vault` to retry indexing."
        )

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
    changed_paths = [p for p in changed_raw.splitlines() if p and _is_supported_ext(p)]
    deleted_paths = [p for p in deleted_raw.splitlines() if p and _is_supported_ext(p)]

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
    try:
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
    except RuntimeError as e:
        return (
            f"Error: {e}\n\n"
            f"Partial progress before the failure: {n_conflicts} conflicts resolved, "
            f"{n_no_conflict} no-conflict files logged. Re-run sync_vault to retry — "
            f"already-processed files will be re-checked safely."
        )

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

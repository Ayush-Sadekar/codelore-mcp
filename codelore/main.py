import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_GITHUB_PREFIXES = ("https://github.com/", "http://github.com/", "git@github.com:")


def is_github_url(arg: str) -> bool:
    return any(arg.startswith(p) for p in _GITHUB_PREFIXES)


def clone_repo(url: str, dest: Path) -> None:
    print(f"Cloning {url} ...", flush=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Error cloning repo:\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


def repo_name_from_url(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    return name.removesuffix(".git")


# ---------------------------------------------------------------------------
# codelore ingest
# ---------------------------------------------------------------------------

def _cmd_ingest(args: argparse.Namespace) -> None:
    from .explain import collect_files, explain_repo, index_repo_questions
    from .generate_questions import get_or_create_collection
    from .ingest import build_tree, write_vault
    from .llm import check_claude_cli

    tmp_dir = None
    try:
        if is_github_url(args.repo):
            tmp_dir = tempfile.mkdtemp(prefix="codelore_")
            repo_root = Path(tmp_dir) / repo_name_from_url(args.repo)
            clone_repo(args.repo, repo_root)
            default_name = repo_name_from_url(args.repo)
        else:
            repo_root = Path(args.repo).resolve()
            if not repo_root.is_dir():
                print(f"Error: {repo_root} is not a directory", file=sys.stderr)
                sys.exit(1)
            default_name = repo_root.name

        vault_root = Path(args.vault).resolve() if args.vault else Path.cwd() / f"{default_name}_vault"
        chroma_path = vault_root.parent / f"{default_name}_chroma"
        explanations_out = vault_root.parent / f"{default_name}_explanations.json"

        all_files = collect_files(repo_root)
        if not all_files:
            print("No supported code files found.")
            return
        file_code_pairs = [(f, f.read_text(encoding="utf-8", errors="ignore")) for f in all_files]
        file_code_pairs = [(f, code) for f, code in file_code_pairs if code.strip()]

        # --- dry-run: estimate and exit ---
        if args.dry_run:
            dirs = {
                str(f.relative_to(repo_root).parent)
                for f, _ in file_code_pairs
                if f.relative_to(repo_root).parent != Path(".")
            }
            print(f"Repo:            {repo_root}")
            print(f"Files found:     {len(file_code_pairs)} supported files")
            print(f"Claude calls:    ~{len(file_code_pairs)} file summaries")
            print(f"                 ~{len(dirs)} directory MOCs")
            print(f"                 + N chunk questions (varies by file size)")
            print(f"Estimated total: ~{len(file_code_pairs) + len(dirs)}+ claude --print calls")
            print(f"\nVault output:    {vault_root}")
            print(f"ChromaDB:        {chroma_path}")
            return

        # --- resolve explanations ---
        file_explanations: dict[str, str] | None = None
        dir_explanations: dict[str, str] | None = None

        if args.explanations:
            ep = Path(args.explanations).resolve()
            if not ep.exists():
                print(f"Error: explanations file not found: {ep}", file=sys.stderr)
                sys.exit(1)
            stored = json.loads(ep.read_text(encoding="utf-8"))
            file_explanations = stored.get("files", stored)
            dir_explanations = stored.get("directories", {})
            print(f"Loaded {len(file_explanations)} file + {len(dir_explanations)} directory explanations from {ep}")

        elif not args.no_llm and explanations_out.exists():
            ans = input(f"\nFound existing explanations at:\n  {explanations_out}\nReuse them? [Y/n] ").strip().lower()
            if ans in ("", "y", "yes"):
                stored = json.loads(explanations_out.read_text(encoding="utf-8"))
                file_explanations = stored.get("files", stored)
                dir_explanations = stored.get("directories", {})
                print(f"Reusing {len(file_explanations)} file + {len(dir_explanations)} directory explanations.")

        if file_explanations is None and not args.no_llm:
            check_claude_cli()
            file_explanations, dir_explanations = explain_repo(repo_root, verbose=True)

            sha_result = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            )
            head_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None
            vault_root.parent.mkdir(parents=True, exist_ok=True)
            explanations_out.write_text(
                json.dumps(
                    {"repo_head_sha": head_sha, "files": file_explanations, "directories": dir_explanations},
                    indent=2, ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print(f"Explanations saved to {explanations_out}")

        # --- parse extra frontmatter fields ---
        extra_frontmatter: dict = {}
        for kv in (args.frontmatter or []):
            if "=" not in kv:
                print(f"Warning: --frontmatter {kv!r} ignored (expected key=value)", file=sys.stderr)
                continue
            k, _, v = kv.partition("=")
            extra_frontmatter[k.strip()] = v.strip()

        # --- build vault ---
        print(f"\nBuilding vault ...")
        index, all_nodes, warnings = build_tree(
            repo_root,
            file_explanations or {},
            dir_explanations or {},
            extra_frontmatter or None,
        )
        write_vault(index, all_nodes, warnings, vault_root, extra_frontmatter or None)

        # --- index ChromaDB (skip in --no-llm mode) ---
        if not args.no_llm:
            import chromadb
            print("Indexing code chunks into ChromaDB ...")
            chroma_client = chromadb.PersistentClient(path=str(chroma_path))
            collection = get_or_create_collection(chroma_client)
            n_questions = index_repo_questions(repo_root, vault_root, file_code_pairs, collection)
            print(f"Indexed {n_questions} questions into {chroma_path}")
        else:
            print("(Skipping ChromaDB index — --no-llm mode)")

        print(f"\nDone. Next steps:")
        print(f"  Query:   codelore query 'how does X work?' --chroma {chroma_path}")
        print(f"  Obsidian: open {vault_root}")

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# codelore query
# ---------------------------------------------------------------------------

def _cmd_query(args: argparse.Namespace) -> None:
    from .query.retrieval import search_chunks

    chroma = args.chroma or os.environ.get("CODELORE_CHROMA_PATH", "")
    vault = args.vault or os.environ.get("CODELORE_VAULT_ROOT", "")

    if not chroma:
        print("Error: --chroma PATH required (or set CODELORE_CHROMA_PATH)", file=sys.stderr)
        sys.exit(1)

    results = search_chunks(args.question, chroma, n_results=args.n)
    if not results:
        print("No results. Has the repo been ingested?")
        return

    print(f"Top {len(results)} results for: \"{args.question}\"\n")
    for i, r in enumerate(results, 1):
        try:
            rel = Path(r.file_path).name
        except Exception:
            rel = r.file_path
        print(f"{i}. {rel}  lines {r.start_line}–{r.end_line}  (distance {r.distance:.3f})")
        print(f"   Q: {r.question}")
        if vault:
            md = Path(r.markdown_path)
            if md.exists():
                lines = md.read_text(encoding="utf-8").splitlines()
                snippet = " ".join(l.strip() for l in lines[3:7] if l.strip())
                if snippet:
                    print(f"   {snippet[:120]}")
        print()


# ---------------------------------------------------------------------------
# codelore init
# ---------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> None:
    vault = getattr(args, "vault", None) or "/path/to/your_repo_vault"
    chroma = getattr(args, "chroma", None) or "/path/to/your_repo_chroma"
    repo = getattr(args, "repo", None) or "/path/to/your_repo"

    config = {
        "mcpServers": {
            "codelore": {
                "command": "codelore-mcp",
                "env": {
                    "CODELORE_VAULT_ROOT": vault,
                    "CODELORE_CHROMA_PATH": chroma,
                    "CODELORE_REPO_ROOT": repo,
                },
            }
        }
    }
    config_src = {
        "mcpServers": {
            "codelore": {
                "command": "python",
                "args": ["/path/to/codelore-clone/mcp_server.py"],
                "env": config["mcpServers"]["codelore"]["env"],
            }
        }
    }

    print("""
codelore setup guide
====================

PREREQUISITES
  • Python 3.11+
  • Claude Code CLI  →  https://claude.ai/download
    Verify: claude --version

INSTALL
  pip install codelore
  # or from source:
  git clone https://github.com/yourname/codelore && pip install -e codelore

STEP 1 — Ingest a repo (run once, reuse explanations on re-runs)
  codelore ingest /path/to/repo
  codelore ingest https://github.com/owner/repo   # also accepts GitHub URLs

  Flags:
    --dry-run          print file count + call estimate, don't run
    --no-llm           structural vault only (no Claude calls)
    --vault PATH       override vault output directory
    --explanations F   load pre-generated explanations JSON

STEP 2 — Add codelore as an MCP server in Claude Code
  Paste one of the blocks below into .claude/settings.json (project-local)
  or ~/.claude/settings.json (global).

  If installed via pip:
""")
    print(json.dumps(config, indent=2))
    print("""
  If running from source:
""")
    print(json.dumps(config_src, indent=2))
    print("""
STEP 3 — (Optional) open the vault in Obsidian
  File → Open Vault → select the *_vault/ directory
  Install "Local REST API" plugin for wikilink traversal via a second MCP server.

STEP 4 — Ask questions in Claude Code
  "Explain this codebase to me"       → triggers explore_repo
  "How does authentication work?"     → triggers search_code
  "What's left to implement?"         → triggers find_todos
  "Show me the vault note for utils/" → triggers read_vault_node

QUERY WITHOUT CLAUDE CODE
  codelore query "how does auth work?" --chroma /path/to/your_repo_chroma
""")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="codelore",
        description="Generate an Obsidian vault + semantic search index from a code repository.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest a repo into a vault + search index")
    p_ingest.add_argument("repo", help="Local path or GitHub URL")
    p_ingest.add_argument("--vault", metavar="PATH", help="Vault output directory (default: <name>_vault)")
    p_ingest.add_argument("--explanations", metavar="PATH", help="Pre-generated explanations JSON (skips LLM summarisation)")
    p_ingest.add_argument("--dry-run", action="store_true", help="Print file count and call estimate without running")
    p_ingest.add_argument("--no-llm", action="store_true", help="Write structural vault without calling Claude")
    p_ingest.add_argument(
        "--frontmatter", metavar="key=value", action="append",
        help="Extra frontmatter field added to every vault note (repeatable, e.g. --frontmatter project=myapp --frontmatter status=draft)",
    )

    # query
    p_query = sub.add_parser("query", help="Semantic search over an ingested repo")
    p_query.add_argument("question", help="Natural language question")
    p_query.add_argument("--vault", metavar="PATH", help="Vault directory (or CODELORE_VAULT_ROOT)")
    p_query.add_argument("--chroma", metavar="PATH", help="ChromaDB directory (or CODELORE_CHROMA_PATH)")
    p_query.add_argument("-n", type=int, default=5, metavar="N", help="Number of results (default: 5)")

    # init
    p_init = sub.add_parser("init", help="Print MCP setup instructions and config snippet")
    p_init.add_argument("--vault", metavar="PATH", help="Pre-fill vault path in generated MCP config")
    p_init.add_argument("--chroma", metavar="PATH", help="Pre-fill chroma path in generated MCP config")
    p_init.add_argument("--repo", metavar="PATH", help="Pre-fill repo path in generated MCP config")

    args = parser.parse_args()

    dispatch = {"ingest": _cmd_ingest, "query": _cmd_query, "init": _cmd_init}
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

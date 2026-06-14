# codelore

Turn any code repository into a searchable Obsidian vault — then let Claude Code navigate it as a set of MCP tools.

codelore runs a two-phase pipeline:
1. **Summarise** — calls `claude --print` once per file and directory to produce structured markdown documentation
2. **Index** — chunks every file at the function/class level, generates developer questions for each chunk, and stores them in a ChromaDB vector index

The result is an Obsidian vault of linked markdown notes and a semantic search index that Claude Code can query as native tools.

---

## How it works

```
your-repo/
    src/auth/middleware.py   →  AI summary + import graph
    src/db/pool.py           →  AI summary + import graph
    ...
           ↓  codelore ingest
your-repo_vault/
    INDEX.md                 overview + wikilinks to all modules
    src/auth/middleware.md   structured summary of every function
    src/db/pool.md           ...
your-repo_chroma/            ChromaDB: chunks indexed by developer questions
```

Claude Code reads `INDEX.md → directory notes → file notes` via the `explore_repo` tool, and answers "how does X work?" questions via `search_code` which hits the semantic index.

---

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** — [claude.ai/download](https://claude.ai/download)
  ```
  claude --version   # must be on PATH
  ```

---

## Install

```bash
pip install codelore
```

Or from source:
```bash
git clone https://github.com/yourname/codelore
pip install -e codelore
```

---

## Quick start

```bash
# 1. Ingest a local repo (or pass a GitHub URL)
codelore ingest /path/to/your-repo

# Preview cost before running on a large repo
codelore ingest /path/to/your-repo --dry-run

# Re-use cached summaries from a previous run (skips claude calls)
codelore ingest /path/to/your-repo   # prompted automatically if cache exists

# 2. Query from the terminal
codelore query "how does authentication work?" \
  --chroma /path/to/your-repo_chroma

# 3. Print MCP setup instructions
codelore init --vault /path/to/your-repo_vault \
              --chroma /path/to/your-repo_chroma \
              --repo /path/to/your-repo
```

---

## CLI reference

### `codelore ingest <repo>`

| Flag | Description |
|---|---|
| `--vault PATH` | Override vault output directory (default: `<name>_vault/`) |
| `--explanations PATH` | Load a pre-generated `_explanations.json` instead of calling Claude |
| `--dry-run` | Print file count and estimated Claude calls without running |
| `--no-llm` | Write structural vault (file tree + imports) without any Claude calls |

### `codelore query <question>`

| Flag | Description |
|---|---|
| `--chroma PATH` | ChromaDB directory (or set `CODELORE_CHROMA_PATH`) |
| `--vault PATH` | Vault directory for summary snippets (or set `CODELORE_VAULT_ROOT`) |
| `-n N` | Number of results (default: 5) |

### `codelore init`

Prints step-by-step setup instructions and a ready-to-paste MCP config block.

| Flag | Description |
|---|---|
| `--vault PATH` | Pre-fill vault path in the generated config |
| `--chroma PATH` | Pre-fill ChromaDB path in the generated config |
| `--repo PATH` | Pre-fill repo root path in the generated config |

---

## MCP server setup (Claude Code)

After ingesting, add codelore as an MCP server so Claude Code can call it as tools.

Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (global):

```json
{
  "mcpServers": {
    "codelore": {
      "command": "codelore-mcp",
      "env": {
        "CODELORE_VAULT_ROOT": "/path/to/your-repo_vault",
        "CODELORE_CHROMA_PATH": "/path/to/your-repo_chroma",
        "CODELORE_REPO_ROOT": "/path/to/your-repo"
      }
    }
  }
}
```

`codelore init` will generate this block with your actual paths filled in.

### Available MCP tools

| Tool | Triggers on |
|---|---|
| `search_code` | "how does X work?", "where is Y defined?" |
| `explore_repo` | "explain this codebase", "give me an overview" |
| `find_todos` | "what's left to implement?", "show open tasks" |
| `read_vault_node` | "show me the summary for src/auth" |
| `read_guidelines` | architectural guidelines doc (optional) |
| `estimate_cost` | "how many claude calls would this take?" |
| `ingest_repo` | "ingest this repo" |
| `rebuild_vault` | rebuild vault from saved explanations |
| `sync_vault` | incremental re-index after code changes |

---

## Supported languages

| Language | Extensions | Chunking |
|---|---|---|
| Python | `.py` | AST (function + class level) |
| JavaScript / TypeScript | `.js` `.jsx` `.ts` `.tsx` `.mjs` | tree-sitter |
| Go | `.go` | tree-sitter |
| Java | `.java` | tree-sitter |
| Kotlin | `.kt` | tree-sitter |
| Scala | `.scala` | tree-sitter |
| C# | `.cs` | tree-sitter |
| Haskell | `.hs` `.lhs` | tree-sitter |
| Elixir | `.ex` `.exs` | tree-sitter |
| Lua | `.lua` | tree-sitter |
| Shell | `.sh` `.bash` | tree-sitter |
| Dart | `.dart` | whole-file |
| R | `.r` `.R` | whole-file |

Non-code files (`.md`, `.json`, `.yaml`, `.toml`, `.sql`, `.proto`, `.graphql`) are also indexed for context.

---

## Incremental re-indexing

After code changes, sync only the modified files instead of re-running the full pipeline:

```
# via MCP tool (in Claude Code):
"sync the vault for /path/to/repo"   →  calls sync_vault(dry_run=True) first

# or directly:
sync_vault(repo_path="/path/to/repo", explanations_json_path="..._explanations.json", dry_run=True)
sync_vault(repo_path="/path/to/repo", explanations_json_path="..._explanations.json", dry_run=False)
```

Requires the repo to be a git repository (uses `git diff` against the SHA saved during ingestion).

---

## Architecture

```
codelore/
  main.py          CLI entry point (ingest / query / init subcommands)
  ingest.py        build file/directory graph, write vault markdown
  explain.py       collect files, call Claude CLI for summaries
  llm.py           Claude CLI wrapper, prompt templates
  nodes.py         FileNode / DirectoryNode / IndexNode → markdown
  generate_questions.py  chunk-level question generation + ChromaDB indexing
  parsers/         language-specific import graph + chunk extraction
    _treesitter.py shared tree-sitter helper
    python.py      stdlib ast
    javascript.py  tree-sitter-javascript / tree-sitter-typescript
    go.py          tree-sitter-go
    jvm.py         tree-sitter-java / tree-sitter-kotlin / tree-sitter-scala
    csharp.py      tree-sitter-c-sharp
    haskell.py     tree-sitter-haskell
    elixir.py      tree-sitter-elixir
    lua.py         tree-sitter-lua
    shell.py       tree-sitter-bash
    ...
  query/
    retrieval.py   search_chunks, bfs_vault, grep_todos, git_file_log
mcp_server.py      FastMCP server exposing 9 tools
```

---

## License

MIT

# Plan: Public Release of codelore

> 2026-06-03 · Status: In progress — MCP server is the primary distribution target

---

## Goal

Make codelore installable and usable by anyone who can clone a repo: expose the pipeline as an MCP server (so Claude Code and other MCP clients can call it as a tool), keep the CLI as a parallel interface for standalone use, and package the tool with a clean README.

The intended usage model is **human + agent working in parallel**: the user reads the Obsidian vault in Obsidian while Claude Code queries and traverses it via MCP tools. Both navigate the same live vault.

---

## Decisions

| Question | Decision | Rationale |
|---|---|---|
| Distribution format | MCP server (primary) + CLI (secondary) | Tool lives in the Claude Code ecosystem; MCP lets Claude compose ingestion, querying, and explanation as native tools |
| LLM backend | Claude Code CLI (`claude --print` subprocess) | Already implemented; zero Anthropic SDK dependency; keeps the tool inside the Claude Code ecosystem |
| API key handling | Handled by Claude Code — no `ANTHROPIC_API_KEY` needed in codelore | `claude` CLI manages auth; codelore just shells out to it |
| Multi-language support | ✅ Done — REGISTRY + CHUNK_REGISTRY cover 11 languages | Already wired in `ingest.py`; no further work needed |
| Package name | ✅ Done — renamed `obsidian_init/` → `codelore/` | Clean public name; relative imports fixed throughout |
| MCP tool granularity | ✅ Done — 8 tools across query + ingestion | `search_code`, `explore_repo`, `find_todos`, `read_vault_node`, `read_guidelines`, `estimate_cost`, `ingest_repo`, `rebuild_vault` |
| Per-call repo paths | ✅ Done — all query tools accept optional path overrides | Single server instance works across multiple repos; no reconfiguration needed |
| Vault traversal | Use Obsidian MCP server alongside codelore | Human reads vault in Obsidian; agent uses Obsidian MCP for wikilink traversal; codelore handles ingestion + semantic search |

---

## Out of Scope

- GUI or web interface
- GitHub Action / CI integration
- Automatic vault sync (re-running on git pull)
- Graph view customization inside Obsidian
- Anthropic SDK / REST API backend (use `claude` CLI instead)

---

## Risks & Flags

| Item | Severity | Note |
|---|---|---|
| `claude` CLI must be on PATH | 🔴 High | Tool is non-functional without Claude Code installed; document this requirement prominently in README |
| LLM cost on large repos | 🟡 Medium | Many `claude --print` subprocesses per repo; needs `--dry-run` or file count warning before running |
| `venv/` committed to repo | 🟢 Low | Add root `.gitignore` before going public |
| No error handling for missing `claude` CLI | 🟡 Medium | `check_claude_cli()` exists in `llm.py` but is only called in some paths; audit all entry points |
| Obsidian MCP is a runtime dependency | 🟡 Medium | Users must have Obsidian open + Local REST API plugin running; codelore cannot bundle this |

---

## Implementation Steps

#### Obsidian MCP Integration (next)
- [ ] Research best Obsidian MCP server (`obsidian-mcp` or similar)
- [ ] Document required Obsidian setup: Local REST API plugin install + port config
- [ ] Decide which codelore vault traversal tools to keep vs. delegate to Obsidian MCP (`read_vault_node`, `explore_repo`)
- [ ] Add `codelore init` CLI command that prints full setup instructions for both codelore + Obsidian MCP
- [ ] Document the two-MCP setup in README: codelore (ingestion + semantic search) + Obsidian MCP (vault traversal)

#### Incremental Re-indexing (future)
- [ ] Diff repo against existing vault/chroma by file mtime or git diff
- [ ] Only re-process files that have changed since last ingest
- [ ] Add `rebuild_vault` smarts: skip ChromaDB chunks for unchanged files

#### CLI (keep working, wire remaining flags)
- [ ] Add `--dry-run` flag: build tree, print file count + estimated Claude CLI calls without running them
- [ ] Add `--no-llm` flag: write vault with structural notes only (skips `claude --print` calls)
- [ ] Audit all entry points (`main.py`, `explain.py`) to ensure `check_claude_cli()` runs before any subprocess call

#### Packaging & Docs
- [ ] Add root-level `.gitignore` covering `venv/`, `__pycache__/`, `*.pyc`, `*_vault/`, `*_chroma/`
- [ ] Write `README.md`: what codelore does, prerequisites, install steps, CLI usage, MCP setup, Obsidian setup
- [ ] Add `LICENSE` file
- [ ] Publish to PyPI so users can `pip install codelore` / `uvx codelore-mcp`

---

## Proof of Concept Queries

Use these to verify that Claude is calling codelore tools rather than answering from its own knowledge. Open a project that has been ingested and ask these in Claude Code.

### Onboarding (should trigger `explore_repo`)
- "Explain this codebase to me"
- "Where should I start if I want to contribute?"
- "What are the main components of this project?"
- "Give me a high-level overview of how this repo is structured"

### Functional / semantic (should trigger `search_code`)
- "How does X work?" (replace X with any feature in the repo)
- "Where is Y defined?" (replace Y with any function or module name)
- "What does this module do?"
- "How does error handling work in this codebase?"

### Progress / tasks (should trigger `find_todos`)
- "What's left to implement?"
- "Which files have open tasks or known issues?"
- "What are the incomplete parts of this project?"
- "Show me what still needs work"

### Vault traversal (should trigger `read_vault_node`)
- "Show me the summary for [any module name]"
- "What does the index say about this repo?"
- "Read the vault note for [any directory name]"

### Multi-hop (should chain tools)
- "Find where [feature] is implemented and show me the vault summary for that file"
- "Search for how [concept] is used and then read the linked vault notes"

### Negative cases (should NOT trigger codelore tools)
- "What is Python?" — general knowledge, no tool call needed
- "How do I use git?" — should answer directly
- "Explain what an API is" — should answer from training data

---

## Open Questions

- **Obsidian MCP choice**: `obsidian-mcp` (npm) vs other options — evaluate which has the best wikilink traversal and note-reading API
- **Tool overlap**: once Obsidian MCP is wired in, `read_vault_node` and `explore_repo` may be redundant — keep as fallback for non-Obsidian users or remove?
- **Incremental re-runs**: Should re-running on a previously ingested repo skip files whose `.md` already exists?

---

## File Tree (current state)

```
codelore-clone/
  codelore/                 ✅ renamed from obsidian_init/
    main.py
    ingest.py
    nodes.py
    llm.py
    explain.py
    generate_questions.py
    parsers/                (11 languages)
    query/
      retrieval.py          ✅ search_chunks, bfs_vault, grep_todos, git_file_log
  obsidian_init/            ← old copy, can be deleted once verified
  mcp_server.py             ✅ 8 tools, per-call path overrides, no sys.path hack
  pyproject.toml            ✅ codelore package, entry points
  requirements.txt          ✅
  .gitignore                ← missing
  README.md                 ← missing
  LICENSE                   ← missing
```

---

## References

- `codelore/llm.py` — `_call_claude()` and `check_claude_cli()` are the Claude Code integration points
- `codelore/main.py` — full pipeline orchestration; entry point for both CLI and MCP wrapping
- `codelore/parsers/__init__.py` — `REGISTRY` and `CHUNK_REGISTRY` for all supported languages
- `mcp_server.py` — all 8 MCP tools; env var config + per-call override pattern
- MCP Python SDK docs: https://github.com/modelcontextprotocol/python-sdk

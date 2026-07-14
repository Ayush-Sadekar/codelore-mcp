# Manual verification: repo-scope guardrails in codelore MCP

Run this on a fresh machine/clone to verify the changes to `mcp_server.py`
(self-scope detection, `get_active_scope` tool, server-level `instructions`)
actually change agent behavior end-to-end. Some of this can't be verified by
unit tests — it depends on how Claude Code, as the calling agent, reacts to
the new `instructions=` text and tool descriptions, which only shows up in a
real session.

`uv run pytest` now covers the pure-logic layer (parsers, sync, `_call_claude`
retry/error handling, MCP scope helpers) — this doc is scoped to what only a
live Claude Code session can verify: agent tool-routing behavior, docstring
adherence, and the actual `claude --print` / ChromaDB / git integration.

## Prerequisites

- Clone this repo fresh (don't reuse a machine that already has stale
  `CODELORE_*` env vars set globally — that would mask bugs).
- `uv` installed, `uv sync` run inside the repo.
- Claude Code installed and able to launch this repo's `.mcp.json`.
- A second, small, *different* git repo available locally to ingest as the
  "target repo" (anything with a few source files works — do not use the
  codelore repo itself for this).

## 1. Baseline (before assuming anything works)

1. Open the codelore repo in Claude Code. Check `.mcp.json` at the repo
   root — confirm the `codelore` server's `env` block does **not** set
   `CODELORE_VAULT_ROOT` / `CODELORE_CHROMA_PATH` / `CODELORE_REPO_ROOT`
   (this is the repo's current out-of-the-box state).
2. In a fresh Claude Code session (cwd = codelore repo), call the
   `explore_repo` tool directly (or ask "explain the repo" and watch what
   tool gets called).
3. Expected: a `RuntimeError` naming the missing env var (e.g.
   `CODELORE_VAULT_ROOT is not set...`). This confirms the "fail loud, don't
   silently misresolve" behavior still works post-change.

## 2. Ingest a second, distinct repo

1. From a terminal (not through Claude Code), run codelore's ingestion
   against the second repo you prepared:
   ```
   uv run codelore ingest /path/to/other-repo
   ```
   or call the `ingest_repo` MCP tool with `repo_path_or_url` pointed at it.
2. Confirm it produces sibling directories named after that repo, e.g.
   `other-repo_vault/` and `other-repo_chroma/`, containing `INDEX.md` and a
   populated ChromaDB directory respectively.
3. Note the absolute paths — you'll point env vars at them next.

## 3. Point the codelore MCP server at the *other* repo

1. Edit `.mcp.json` in the codelore repo (the one Claude Code is actually
   running from) and add to the `codelore` server's `env` block:
   ```json
   "CODELORE_VAULT_ROOT": "/absolute/path/to/other-repo_vault",
   "CODELORE_CHROMA_PATH": "/absolute/path/to/other-repo_chroma",
   "CODELORE_REPO_ROOT": "/absolute/path/to/other-repo"
   ```
2. Restart the MCP server (restart Claude Code, or however your client
   reloads `.mcp.json`) so the new env vars take effect.
3. Call the new `get_active_scope` tool directly with no arguments.
   **Expected:** a markdown table showing all three fields resolved to the
   *other* repo's paths, status `OK` for each, no `SELF-SCOPE`/`MISSING`
   warnings.
4. Call `explore_repo` with no arguments. **Expected:** it succeeds and
   returns a structure describing the *other* repo (its actual files/dirs),
   not codelore's own `mcp_server.py`/`main.py`/`codelore/` package layout.

## 4. Misconfiguration / self-scope detection

1. Temporarily change `.mcp.json`'s `CODELORE_REPO_ROOT` to point at the
   codelore repo's own path (i.e. the directory containing `mcp_server.py`),
   leaving `CODELORE_VAULT_ROOT`/`CODELORE_CHROMA_PATH` as they were from
   step 3. Restart the MCP server.
2. Call `get_active_scope`. **Expected:** the `repo_root` row shows status
   `SELF-SCOPE`, with a message explaining the path resolves inside
   codelore's own source tree and instructing the agent to ask the user
   rather than fall back to grepping cwd.
3. Call `find_todos` (which uses `repo_root`) directly. **Expected:** it
   raises the same self-scope `RuntimeError` rather than silently scanning
   codelore's own source for TODOs.
4. Revert `.mcp.json`'s `CODELORE_REPO_ROOT` back to the other repo's path
   and restart the server before continuing.

## 5. The actual behavioral fix — agent tool selection

This is the step that verifies the original bug is fixed; nothing in steps
1-4 can guarantee it, since it depends on how the calling agent reacts to
`instructions=`, not on Python logic.

1. Start a **brand new** Claude Code session with cwd = the codelore repo
   (not the other repo), env vars still correctly pointed at the other repo
   from step 3.
2. Ask: **"Explain this repo's architecture."**
3. Observe which tools get called. **Expected:** the agent calls
   `explore_repo` (and/or `get_active_scope` first to confirm scope) rather
   than using `Read`/`Glob`/`Grep`/`Bash` on the working directory. The
   final answer describes the *other* repo's structure, not codelore's own
   `mcp_server.py`/`codelore/` package.
4. If the agent instead reads cwd directly: check that `.mcp.json` actually
   picked up the `instructions` field (some MCP clients cache server
   metadata — a full restart, not just a new chat, may be required), then
   retry. If it still happens, that's a real finding to report back — it
   means `instructions=` isn't sufficient nudging on its own and stronger
   measures (e.g. explicit user-facing CLAUDE.md reminder in the *other*
   repo, per the plan's noted future nice-to-have) may be needed.

## 6. Regression check on the happy path

1. With env vars still correctly pointed at the other repo, call
   `search_code`, `explore_repo`, and `find_todos` each with a real query
   relevant to that repo.
2. Confirm the *shape* of the returned markdown (headings, table columns,
   result formatting) is unchanged from before this change — only extra
   guidance text was added to docstrings/instructions, not to the actual
   return values.

## 7. Vault-insufficient case (reading real source when the vault falls short)

1. Ask a question whose answer requires exact, current detail a vault
   summary is unlikely to capture verbatim — e.g. "what's the exact
   parameter list of function X right now" for some function in the other
   repo.
2. **Expected:** the agent calls `search_code` (or `explore_repo`) first to
   locate the relevant file via its absolute `file_path`, then reads that
   *specific file* directly (Read/Grep) to get the exact current signature —
   rather than (a) guessing from the vault summary's prose, or (b) reading
   arbitrary files in the current working directory instead of the resolved
   target repo.
3. This confirms the softened `search_code` FALLBACK docstring didn't
   overcorrect back into "never touch source" — targeted reads of the
   resolved target repo, once codelore has pointed at a file, should still
   happen freely.

## Reporting results

For each numbered section, note pass/fail and paste the actual tool output
where it diverges from "Expected." Section 5 is the one most likely to need
follow-up — it's the part of this fix that depends on agent behavior, not
just code.

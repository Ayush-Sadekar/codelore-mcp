"""
codelore MCP server package — exposes repo ingestion and query tools to Claude Code.

HOW THIS PACKAGE IS ORGANIZED
  scope.py        — config helpers: read env vars OR accept per-call overrides
  query_tools.py  — read from an already-ingested vault/ChromaDB index
  ingest_tools.py — run the full pipeline to create a vault/index from scratch

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
from mcp.server.fastmcp import FastMCP

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

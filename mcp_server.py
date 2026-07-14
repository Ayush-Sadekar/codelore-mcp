"""
codelore MCP server entrypoint — exposes repo ingestion and query tools to
Claude Code.

Tool implementations live in codelore/mcp/{scope,query_tools,ingest_tools}.py.
Importing those submodules registers their @mcp.tool()-decorated functions
against the shared `mcp` instance defined in codelore/mcp/__init__.py.
"""
from codelore.mcp import mcp
from codelore.mcp import scope, query_tools, ingest_tools  # noqa: F401 — registers tools

if __name__ == "__main__":
    mcp.run()

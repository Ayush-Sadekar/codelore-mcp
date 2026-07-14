"""
Config/scope-resolution helpers shared by query_tools.py and ingest_tools.py.

Each helper accepts an optional override string. If the caller passes a
non-empty string, it's used directly. Otherwise we fall back to the env var.
This lets the query tools accept per-call paths while still working with
the env-var convention when no override is provided.
"""
import os
from pathlib import Path

from . import mcp

_CODELORE_PKG_DIR = Path(__file__).resolve().parent.parent.parent


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"codelore: {name} is not set. Pass it directly to the tool or "
            "configure it as an environment variable in your MCP client config."
        )
    return val


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

import re
from pathlib import Path

_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")

# function_declaration covers: function foo(), local function foo(), function Obj:method()
_CHUNK_TYPES: set[str] = {"function_declaration"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _REQUIRE_RE.finditer(source):
        raw = m.group(1)
        r = _resolve(raw, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved require {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_lua as tslua
        from tree_sitter import Language
        return Language(tslua.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve(raw: str, repo_root: Path) -> Path | None:
    # dots are path separators in Lua: 'utils.helper' -> utils/helper.lua
    parts = raw.replace(".", "/")
    candidate = (repo_root / parts).with_suffix(".lua")
    if candidate.exists():
        try:
            return candidate.relative_to(repo_root)
        except ValueError:
            pass
    return None

import re
from pathlib import Path

_IMPORT_RE = re.compile(r'^import\s+(?:qualified\s+)?([\w.]+)', re.MULTILINE)

_CHUNK_TYPES: set[str] = {"function", "signature"}  # top-level fn definitions + type sigs


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _IMPORT_RE.finditer(source):
        raw = m.group(1)
        r = _resolve(raw, repo_root)
        if r:
            resolved.append(r)

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_haskell as tshaskell
        from tree_sitter import Language
        return Language(tshaskell.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve(dotted: str, repo_root: Path) -> Path | None:
    parts = dotted.split(".")
    for ext in [".hs", ".lhs"]:
        candidate = repo_root.joinpath(*parts).with_suffix(ext)
        if candidate.exists():
            try:
                return candidate.relative_to(repo_root)
            except ValueError:
                pass
    return None

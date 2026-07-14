from pathlib import Path

from .c import _INCLUDE_RE, resolve_include

_CHUNK_TYPES: set[str] = {"function_definition", "class_specifier"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _INCLUDE_RE.finditer(source):
        raw = m.group(1)
        r = resolve_include(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved include {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language
        return Language(tscpp.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)

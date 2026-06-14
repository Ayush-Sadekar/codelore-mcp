import re
from pathlib import Path

_SOURCE_RE = re.compile(r'^(?:source|\.)\s+([^\s#;]+)', re.MULTILINE)

_CHUNK_TYPES: set[str] = {"function_definition"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _SOURCE_RE.finditer(source):
        raw = m.group(1).strip('"\'')
        r = _resolve(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved source {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_bash as tsbash
        from tree_sitter import Language
        return Language(tsbash.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve(raw: str, from_file: Path, repo_root: Path) -> Path | None:
    candidate = (from_file.parent / raw).resolve()
    if candidate.exists():
        try:
            return candidate.relative_to(repo_root)
        except ValueError:
            pass
    return None

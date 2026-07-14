import re
from pathlib import Path

_INCLUDE_RE = re.compile(
    r"""(?:require_once|include_once|require|include)\s*\(?\s*['"]([^'"]+)['"]""",
)
_USE_RE = re.compile(r'^\s*use\s+([\w\\]+)\s*;', re.MULTILINE)

_CHUNK_TYPES: set[str] = {"function_definition", "method_declaration", "class_declaration"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _INCLUDE_RE.finditer(source):
        raw = m.group(1)
        r = _resolve_path(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved include {raw!r} in {file_path.relative_to(repo_root)}")

    for m in _USE_RE.finditer(source):
        r = _resolve_namespace(m.group(1), repo_root)
        if r:
            resolved.append(r)
        # unresolved `use` targets are usually vendor/autoloaded classes — skip silently

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_php as tsphp
        from tree_sitter import Language
        return Language(tsphp.language_php())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve_path(raw: str, from_file: Path, repo_root: Path) -> Path | None:
    for base in (from_file.parent, repo_root):
        candidate = base / raw
        if not candidate.suffix:
            candidate = candidate.with_suffix(".php")
        if candidate.exists():
            try:
                return candidate.resolve().relative_to(repo_root.resolve())
            except ValueError:
                pass
    return None


def _resolve_namespace(dotted: str, repo_root: Path) -> Path | None:
    parts = dotted.split("\\")
    candidate = repo_root.joinpath(*parts).with_suffix(".php")
    if candidate.exists():
        try:
            return candidate.relative_to(repo_root)
        except ValueError:
            pass
    return None

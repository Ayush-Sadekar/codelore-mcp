import re
from pathlib import Path

_REQUIRE_RE = re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""", re.MULTILINE)
_REQUIRE_RELATIVE_RE = re.compile(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", re.MULTILINE)

_CHUNK_TYPES: set[str] = {"method", "singleton_method", "class", "module"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _REQUIRE_RELATIVE_RE.finditer(source):
        raw = m.group(1)
        r = _resolve_relative(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved require_relative {raw!r} in {file_path.relative_to(repo_root)}")

    for m in _REQUIRE_RE.finditer(source):
        raw = m.group(1)
        r = _resolve_repo(raw, repo_root)
        if r:
            resolved.append(r)
        # gem/stdlib requires silently skipped — no warning needed

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_ruby as tsruby
        from tree_sitter import Language
        return Language(tsruby.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve_relative(raw: str, from_file: Path, repo_root: Path) -> Path | None:
    candidate = (from_file.parent / raw).with_suffix(".rb")
    if candidate.exists():
        try:
            return candidate.resolve().relative_to(repo_root.resolve())
        except ValueError:
            pass
    return None


def _resolve_repo(raw: str, repo_root: Path) -> Path | None:
    # `require` looks up $LOAD_PATH, which conventionally includes a top-level lib/ dir.
    for base in (repo_root, repo_root / "lib"):
        candidate = base.joinpath(raw).with_suffix(".rb")
        if candidate.exists():
            try:
                return candidate.relative_to(repo_root)
            except ValueError:
                pass
    return None

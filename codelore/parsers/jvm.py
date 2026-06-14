"""Parser for Java, Kotlin, and Scala — all share the same import syntax."""
import re
from pathlib import Path

_IMPORT_RE = re.compile(r'^import\s+(?:static\s+)?([\w.]+)', re.MULTILINE)
_EXTENSIONS = {".java": ".java", ".kt": ".kt", ".scala": ".scala"}

_JAVA_CHUNK_TYPES: set[str] = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "method_declaration",
    "constructor_declaration",
}
_KOTLIN_CHUNK_TYPES: set[str] = {
    "class_declaration",
    "function_declaration",
    "object_declaration",
    "companion_object",
}
_SCALA_CHUNK_TYPES: set[str] = {
    "class_definition",
    "object_definition",
    "trait_definition",
    "function_definition",
}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    ext = file_path.suffix
    resolved, warnings = [], []

    for m in _IMPORT_RE.finditer(source):
        raw = m.group(1).rstrip(".*")  # strip wildcard imports
        r = _resolve(raw, repo_root, ext)
        if r:
            resolved.append(r)

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    ext = file_path.suffix
    if ext == ".java":
        def _lang():
            import tree_sitter_java as tsjava
            from tree_sitter import Language
            return Language(tsjava.language())
        types = _JAVA_CHUNK_TYPES
    elif ext == ".kt":
        def _lang():
            import tree_sitter_kotlin as tskotlin
            from tree_sitter import Language
            return Language(tskotlin.language())
        types = _KOTLIN_CHUNK_TYPES
    elif ext == ".scala":
        def _lang():
            import tree_sitter_scala as tsscala
            from tree_sitter import Language
            return Language(tsscala.language())
        types = _SCALA_CHUNK_TYPES
    else:
        return whole_file_fallback(file_path)
    result = ts_parse_chunks(file_path, _lang, types)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve(dotted: str, repo_root: Path, original_ext: str) -> Path | None:
    parts = dotted.split(".")
    # try all JVM extensions, preferring the same as the source file
    exts = [original_ext] + [e for e in _EXTENSIONS.values() if e != original_ext]
    for ext in exts:
        candidate = repo_root.joinpath(*parts).with_suffix(ext)
        if candidate.exists():
            try:
                return candidate.relative_to(repo_root)
            except ValueError:
                pass
    return None

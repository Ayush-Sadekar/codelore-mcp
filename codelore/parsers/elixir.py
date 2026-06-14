import re
from pathlib import Path

_IMPORT_RE = re.compile(r'^(?:alias|import|use|require)\s+([\w.]+)', re.MULTILINE)

# In Elixir's tree-sitter grammar, def/defp/defmodule etc. are `call` nodes
# whose first child identifier holds the keyword.
_DEF_NAMES: frozenset[bytes] = frozenset([b"def", b"defp", b"defmodule", b"defmacro", b"defmacrop"])


def _collect_elixir(node, lines: list[str], out: list) -> None:
    if (
        node.type == "call"
        and node.children
        and node.children[0].type == "identifier"
        and node.children[0].text in _DEF_NAMES
    ):
        start = node.start_point[0] + 1
        end = node.end_point[0] + 1
        out.append((start, end, "\n".join(lines[start - 1:end])))
        # Recurse so nested defs inside defmodule are also captured.
    for child in node.children:
        _collect_elixir(child, lines, out)


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
        import tree_sitter_elixir as tselixir
        from tree_sitter import Language
        return Language(tselixir.language())
    result = ts_parse_chunks(file_path, _lang, set(), collect_fn=_collect_elixir)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve(dotted: str, repo_root: Path) -> Path | None:
    # MyApp.Utils -> lib/my_app/utils.ex (snake_case convention)
    parts = [_to_snake(p) for p in dotted.split(".")]
    for base in [repo_root, repo_root / "lib"]:
        for ext in [".ex", ".exs"]:
            candidate = base.joinpath(*parts).with_suffix(ext)
            if candidate.exists():
                try:
                    return candidate.relative_to(repo_root)
                except ValueError:
                    pass
    return None


def _to_snake(name: str) -> str:
    import re
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

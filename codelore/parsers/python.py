import ast
from pathlib import Path


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    """Return (start_line, end_line, chunk_text) for every function and class in the file."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno        # 1-indexed, inclusive
            end = node.end_lineno      # 1-indexed, inclusive
            chunk_text = "\n".join(lines[start - 1:end])
            chunks.append((start, end, chunk_text))
    return chunks


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        return [], [f"SyntaxError in {file_path.relative_to(repo_root)}: {e}"]
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                r = _resolve(alias.name, file_path, repo_root, level=0)
                if r:
                    resolved.append(r)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level
            r = _resolve(module, file_path, repo_root, level=level)
            if r:
                resolved.append(r)
            elif level > 0:
                raw = ("." * level) + module
                warnings.append(f"Unresolved relative import {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


def _resolve(module: str, from_file: Path, repo_root: Path, level: int) -> Path | None:
    parts = module.split(".") if module else []

    if level > 0:
        base = from_file.parent
        for _ in range(level - 1):
            base = base.parent
        candidates = _candidates(base, parts)
    else:
        candidates = _candidates(repo_root, parts) + _candidates(from_file.parent, parts)

    for c in candidates:
        if c.exists() and _is_inside(c, repo_root):
            return c.relative_to(repo_root)

    return None


def _candidates(base: Path, parts: list[str]) -> list[Path]:
    if not parts:
        return []
    sub = base.joinpath(*parts)
    return [sub.with_suffix(".py"), sub / "__init__.py"]


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

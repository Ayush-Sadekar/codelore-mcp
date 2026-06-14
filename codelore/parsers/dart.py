import re
from pathlib import Path

_IMPORT_RE = re.compile(r"""^import\s+['"]([^'"]+)['"]""", re.MULTILINE)


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _IMPORT_RE.finditer(source):
        raw = m.group(1)
        if raw.startswith("dart:") or raw.startswith("package:"):
            continue  # stdlib or pub package — skip silently

        r = _resolve(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved import {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


def _resolve(raw: str, from_file: Path, repo_root: Path) -> Path | None:
    candidate = (from_file.parent / raw).resolve()
    if candidate.exists():
        try:
            return candidate.relative_to(repo_root)
        except ValueError:
            pass
    return None

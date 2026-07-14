from pathlib import Path

from codelore.parsers.python import parse_chunks, parse_imports


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_chunks_top_level_function_and_class(tmp_path):
    src = (
        "def foo():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Bar:\n"
        "    def method(self):\n"
        "        return 2\n"
    )
    f = _write(tmp_path, "mod.py", src)

    chunks = parse_chunks(f, tmp_path)

    names_by_range = {(start, end): text for start, end, text in chunks}
    assert (1, 2) in names_by_range
    assert names_by_range[(1, 2)] == "def foo():\n    return 1"

    # ast.walk visits the class and its nested method; class spans lines 5-7,
    # method spans lines 6-7.
    assert (5, 7) in names_by_range
    assert (6, 7) in names_by_range


def test_parse_chunks_on_syntax_error_returns_empty(tmp_path):
    f = _write(tmp_path, "broken.py", "def foo(:\n    pass\n")
    assert parse_chunks(f, tmp_path) == []


def test_parse_imports_absolute_import(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "helper.py").write_text("X = 1\n", encoding="utf-8")

    f = _write(tmp_path, "main.py", "from pkg.helper import X\n")
    resolved, warnings = parse_imports(f, tmp_path)

    assert warnings == []
    assert resolved == [Path("pkg/helper.py")]


def test_parse_imports_unresolved_relative_import_warns(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    f = _write(tmp_path, "pkg/mod.py", "from . import does_not_exist\n")
    resolved, warnings = parse_imports(tmp_path / "pkg" / "mod.py", tmp_path)

    assert any("Unresolved relative import" in w for w in warnings)


def test_parse_imports_syntax_error_returns_warning(tmp_path):
    f = _write(tmp_path, "broken.py", "def foo(:\n    pass\n")
    resolved, warnings = parse_imports(f, tmp_path)
    assert resolved == []
    assert any("SyntaxError" in w for w in warnings)

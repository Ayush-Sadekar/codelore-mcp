import pytest

from codelore.mcp.ingest_tools import _is_github_url, _is_supported_ext, _repo_name_from_url
from codelore.mcp.scope import _CODELORE_PKG_DIR, _check_not_self_scope


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "http://github.com/owner/repo",
        "git@github.com:owner/repo.git",
    ],
)
def test_is_github_url_true(url):
    assert _is_github_url(url) is True


@pytest.mark.parametrize(
    "arg",
    ["/local/path/to/repo", "owner/repo", "https://gitlab.com/owner/repo"],
)
def test_is_github_url_false(arg):
    assert _is_github_url(arg) is False


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo", "repo"),
        ("https://github.com/owner/repo.git", "repo"),
        ("https://github.com/owner/repo/", "repo"),
        ("git@github.com:owner/repo.git", "repo"),
    ],
)
def test_repo_name_from_url(url, expected):
    assert _repo_name_from_url(url) == expected


def test_check_not_self_scope_passes_through_unrelated_path(tmp_path):
    other = tmp_path / "some-other-repo"
    other.mkdir()
    assert _check_not_self_scope("repo_root", str(other)) == str(other)


def test_check_not_self_scope_raises_on_codelore_own_source():
    with pytest.raises(RuntimeError, match="own source tree"):
        _check_not_self_scope("repo_root", str(_CODELORE_PKG_DIR))


def test_check_not_self_scope_raises_on_path_inside_codelore():
    inside = _CODELORE_PKG_DIR / "codelore"
    with pytest.raises(RuntimeError, match="own source tree"):
        _check_not_self_scope("repo_root", str(inside))


@pytest.mark.parametrize("rel_path", ["src/main.py", "lib/foo.rs", "README.md"])
def test_is_supported_ext_true(rel_path):
    assert _is_supported_ext(rel_path) is True


@pytest.mark.parametrize("rel_path", ["image.png", "archive.zip", "no_extension"])
def test_is_supported_ext_false(rel_path):
    assert _is_supported_ext(rel_path) is False

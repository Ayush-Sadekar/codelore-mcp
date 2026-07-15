import chromadb
import pytest

from codelore.generate_questions import get_or_create_collection
from codelore.mcp.query_tools import search_code


@pytest.fixture
def seeded_env(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "parser.md").write_text("# parser notes")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    chroma_path = tmp_path / "chroma"
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = get_or_create_collection(client)
    collection.upsert(
        ids=["1"],
        documents=["how does the parser tokenize input?"],
        metadatas=[{
            "file_path": str(repo_root / "parser.py"),
            "start_line": 1,
            "end_line": 10,
            "markdown_path": str(vault_root / "parser.md"),
        }],
    )

    monkeypatch.setenv("CODELORE_VAULT_ROOT", str(vault_root))
    monkeypatch.setenv("CODELORE_CHROMA_PATH", str(chroma_path))
    monkeypatch.setenv("CODELORE_REPO_ROOT", str(repo_root))


def test_search_code_labels_on_topic_query_high_confidence(seeded_env):
    output = search_code("how does tokenization work")
    assert "**Confidence:** high" in output
    assert "consider falling back" not in output


def test_search_code_labels_off_topic_query_low_confidence(seeded_env):
    output = search_code("how do I bake a chocolate cake")
    assert "**Confidence:** low" in output
    assert "consider falling back" in output


def test_search_code_max_distance_is_configurable(seeded_env):
    # An extremely strict threshold should push even a strong match to low confidence.
    output = search_code("how does tokenization work", max_distance=0.0)
    assert "**Confidence:** low" in output

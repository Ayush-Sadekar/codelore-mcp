import chromadb
import pytest

from codelore.embedding import EMBEDDING_MODEL_NAME
from codelore.generate_questions import get_or_create_collection
from codelore.query.retrieval import _get_collection, search_chunks


def _seed_collection(chroma_path: str, repo_dir) -> None:
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = get_or_create_collection(client)
    collection.upsert(
        ids=["1"],
        documents=["how does the parser tokenize input?"],
        metadatas=[{
            "file_path": str(repo_dir / "parser.py"),
            "start_line": 1,
            "end_line": 10,
            "markdown_path": str(repo_dir / "parser.md"),
        }],
    )


def test_ingested_collection_records_embedding_model(tmp_path):
    chroma_path = tmp_path / "chroma"
    _seed_collection(chroma_path, tmp_path)

    collection = _get_collection(str(chroma_path))
    assert collection.metadata["embedding_model"] == EMBEDDING_MODEL_NAME


def test_search_chunks_round_trip_uses_shared_embedding_function(tmp_path):
    chroma_path = tmp_path / "chroma"
    _seed_collection(chroma_path, tmp_path)

    results = search_chunks("how does tokenization work", str(chroma_path), n_results=1)

    assert len(results) == 1
    assert results[0].question == "how does the parser tokenize input?"


def test_get_collection_raises_actionable_error_on_embedding_mismatch(tmp_path):
    chroma_path = tmp_path / "chroma"
    client = chromadb.PersistentClient(path=str(chroma_path))
    client.get_or_create_collection(
        name="code_chunks",
        metadata={"hnsw:space": "cosine", "embedding_model": "some-other-model"},
    )

    with pytest.raises(RuntimeError) as exc_info:
        _get_collection(str(chroma_path))

    message = str(exc_info.value)
    assert "NOT a repo-scope problem" in message
    assert "repo_root" in message

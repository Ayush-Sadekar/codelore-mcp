"""
Shared embedding function for the ChromaDB `code_chunks` collection.

Both ingestion (generate_questions.py) and query (query/retrieval.py) must use
the exact same embedding model — otherwise the vectors written at ingest time
and the vectors used to search at query time live in different spaces, and
nearest-neighbor results become meaningless without any error being raised.
Chroma's own "default" embedding function is not pinned to a specific model
across versions, so we pin one explicitly here rather than relying on it.
"""
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


def get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    return SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL_NAME)

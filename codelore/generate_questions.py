"""
Generate jeopardy-style questions for code chunks and index them into ChromaDB.

Each chunk (function, class, block) produces a set of questions stored as
separate ChromaDB documents so individual queries match the most relevant chunk.
Metadata — not the document content — carries all location/path info.
"""
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import chromadb

QUESTION_PROMPT = (
    "You are an advanced technical parser. Read the following source code documentation chunk and generate numerous factual questions only. "
    "These questions must mirror authentic developer search queries, IDE lookups, and natural language coding variations. "
    "Do NOT include answers — output questions only.\n\n"

    "CRITICAL QUERY TYPES TO INCLUDE:\n"
    "1. Exact Invocations: Shorter, direct queries targeting explicit names (e.g., 'how to call verify_state_mutation', 'parameters for BoundedExecutor').\n"
    "2. Abstract Functionality: Longer, goal-oriented natural style queries (e.g., 'how do we handle thread concurrency when workers scale', 'where is the connection timeout configured').\n"
    "3. State & Side Effects: Queries about data modification (e.g., 'does this function mutate the active session cache', 'what happens if the memory flag is set to true').\n"
    "4. Troubleshooting & Errors: Error-symptom queries (e.g., 'why does the pipeline throw an invalid state exception', 'handling SIGSEGV during mutation runs').\n\n"

    "RULES:\n"
    "- Incorporate 'how', 'why', and 'where' questions to reflect genuine developer debugging curiosity.\n"
    "- Avoid using artificial phrases like 'according to the text' or 'in this code'.\n"
    "- Specify exact class, function, or variable names instead of using ambiguous pronouns.\n"
    "- Maintain a natural, realistic developer query style.\n"
    "- FORMATTING: Output one question per line. No bullets, numbering, answers, or commentary. Each line must be a standalone question ending with '?'.\n\n"

    "Code Chunk:\n"
    "```\n"
    "{CHUNK}\n"
    "```"
)


@dataclass
class ChunkMetadata:
    file_path: str        # absolute path to the source file
    start_line: int       # 1-indexed, inclusive
    end_line: int         # 1-indexed, inclusive
    markdown_path: str    # absolute path to the .md summary for this file

# calls claude code cli to generate the questions 
def _call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "--print", prompt],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()

# parses claude's response to get the questions 
def _parse_questions(raw: str) -> list[str]:
    questions = []
    for line in raw.splitlines():
        line = line.strip()
        if line.endswith("?") and len(line) > 10:
            questions.append(line)
    return questions


def generate_questions_for_chunk(chunk_text: str, meta: ChunkMetadata) -> dict:
    """
    Call Claude CLI to generate questions for a code chunk.

    Returns:
        {
            "questions": ["q1", "q2", ...],
            "metadata": {
                "file_path": ...,
                "start_line": ...,
                "end_line": ...,
                "markdown_path": ...,
            }
        }
    """
    prompt = QUESTION_PROMPT.format(CHUNK=chunk_text)
    raw = _call_claude(prompt)
    questions = _parse_questions(raw)

    return {
        "questions": questions,
        "metadata": {
            "file_path": meta.file_path,
            "start_line": meta.start_line,
            "end_line": meta.end_line,
            "markdown_path": meta.markdown_path,
        },
    }


def index_chunk(
    collection: chromadb.Collection,
    chunk_text: str,
    meta: ChunkMetadata,
) -> list[str]:
    """
    Generate questions for a chunk and upsert each question as a separate
    ChromaDB document. All documents share the same chunk metadata.

    Returns the list of document IDs added.
    """
    result = generate_questions_for_chunk(chunk_text, meta)
    questions: list[str] = result["questions"]
    metadata: dict = result["metadata"]

    if not questions:
        return []

    ids = [str(uuid.uuid4()) for _ in questions]
    collection.upsert(
        ids=ids,
        documents=questions,
        metadatas=[metadata] * len(questions),
    )
    return ids


def get_or_create_collection(
    client: chromadb.ClientAPI,
    collection_name: str = "code_chunks",
) -> chromadb.Collection:
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

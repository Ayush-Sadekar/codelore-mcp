import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import chromadb

from .generate_questions import ChunkMetadata, index_chunk
from .ingest import IGNORE_DIRS
from .llm import check_claude_cli, summarize_directories, summarize_files
from .parsers import CHUNK_REGISTRY, REGISTRY

MAX_CHARS = 12_000


def collect_files(repo_root: Path) -> list[Path]:
    files = []
    for f in sorted(repo_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(repo_root)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if f.suffix not in REGISTRY:
            continue
        files.append(f)
    return files


def read_code(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")[:MAX_CHARS]
    except UnicodeDecodeError:
        return ""


def index_repo_questions(
    repo_root: Path,
    vault_root: Path,
    file_code_pairs: list[tuple[Path, str]],
    collection: chromadb.Collection,
    max_workers: int = 8,
) -> int:
    """
    Chunk every file and index questions into ChromaDB.

    Each chunk becomes N documents (one per question) with metadata pointing
    back to the source file, line range, and its vault markdown summary.
    Returns the total number of questions indexed.
    """
    def index_file(pair: tuple[Path, str]) -> int:
        file_path, _ = pair
        chunk_parser = CHUNK_REGISTRY.get(file_path.suffix)
        if chunk_parser is None:
            return 0

        chunks = chunk_parser(file_path, repo_root)
        rel = file_path.relative_to(repo_root)
        markdown_path = str(vault_root / rel.with_suffix(".md"))

        count = 0
        for start_line, end_line, chunk_text in chunks:
            if not chunk_text.strip():
                continue
            meta = ChunkMetadata(
                file_path=str(file_path),
                start_line=start_line,
                end_line=end_line,
                markdown_path=markdown_path,
            )
            ids = index_chunk(collection, chunk_text, meta)
            count += len(ids)
        return count

    total = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(index_file, pair): pair for pair in file_code_pairs}
        for fut in as_completed(futures):
            total += fut.result()
    return total


def explain_repo(repo_root: Path, verbose: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (file_summaries, dir_summaries)."""
    all_files = collect_files(repo_root)
    if not all_files:
        print("No supported code files found.")
        return {}, {}

    file_code_pairs = [(f, read_code(f)) for f in all_files]
    file_code_pairs = [(f, code) for f, code in file_code_pairs if code.strip()]

    print(f"Explaining {len(file_code_pairs)} files via Claude CLI ...")
    file_summaries = summarize_files(file_code_pairs, repo_root, verbose=verbose)

    print(f"Generating {len(set(str(Path(f.relative_to(repo_root)).parent) for f, _ in file_code_pairs if Path(f.relative_to(repo_root)).parent != Path('.')))} directory MOCs via Claude CLI ...")
    dir_summaries = summarize_directories(file_summaries, repo_root, verbose=verbose)

    return file_summaries, dir_summaries


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 explain.py <repo_path> [output.json]")
        sys.exit(1)

    check_claude_cli()

    repo_root = Path(sys.argv[1]).resolve()
    if not repo_root.is_dir():
        print(f"Error: {repo_root} is not a directory")
        sys.exit(1)

    output_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else repo_root.parent / "explanations.json"

    explanations = explain_repo(repo_root)

    output_path.write_text(json.dumps(explanations, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Explanations written to {output_path} ({len(explanations)} files)")


if __name__ == "__main__":
    main()

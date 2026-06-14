import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROMPT_TEMPLATE = """\
You are an expert Principal Software Architect. Your job is to analyze the provided source code file and generate a highly structured, deep semantic documentation entry for an LLM-Wiki.

You must explicitly document every major Class, Method, and Function within this file. This breakdown is critical because it directly seeds a downstream chunk-based vector search engine.

File Name: {file_name}
Path: {file_path}

Source Code:
{code_content}


Generate your response in the exact Markdown layout specified below. Do not add any conversational introduction or conclusion.

### Target Markdown Output Template:

# File: {file_name}

## 🎯 High-Level Purpose
**Core Responsibility:** [1-2 sentences explaining exactly *why* this file exists and the overarching problem it solves in the codebase.]

**Dependencies & Links:**
- Internal imports/calls: [e.g., `[[pool.py]]`, `[[state_machine.py]]`]
- External packages used: [e.g., `FastMCP`, `chromadb`]

---

## 🏗️ Structural Breakdown

[CRITICAL: Iterate through every significant Class, Method, and Function found in the code. Create a separate '###' subsection for each one using the exact format below.]

### [Insert Type: e.g., Class / Method / Function] -> `name_of_element()`
- **Functional Description:** [1-2 sentences detailing what this specific block executes or manages.]
- **Input / Output Contracts:** [List parameters, expected types, return values, and any error/exception behaviors raised here.]
- **Design Constraints:** [Identify thread-safety rules, performance characteristics, mutability states, or dependency gotchas unique to this specific block.]

#### Questions this section answers:
- [Write an explicit developer question targeting the exact utility or purpose of this specific function/class]
- [Write a specific troubleshooting/error or edge-case question that a developer modifying this specific block would ask]

[Repeat the '###' block structure above for the next class, method, or function until the file is completely mapped.]\
"""


def check_claude_cli() -> None:
    if shutil.which("claude") is None:
        print("Error: 'claude' CLI not found on PATH. Install Claude Code to use this tool.")
        sys.exit(1)


def _call_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "--print", prompt],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


AGGREGATE_INDEX_PROMPT = """\
You are an expert System Engineer tasked with creating a high-level Map of Content (MOC) for a subsystem directory within our codebase.

You will be given a list of architectural summaries for files located within this specific directory. Your job is to synthesize these modular components into a cohesive structural overview of the entire directory layer.

Directory Name: {directory_name}
Relative Path: {directory_path}

Collected Sub-Component Summaries:
{child_summaries}

Generate your response in the exact Markdown layout specified below. Do not add conversational fluff.

### Target Markdown Output Template:

# Subsystem Index: {directory_name}

## 🗺️ Architectural Topology
[Provide a 3-sentence summary of how the modules inside this directory interact with each other to serve the broader application. Define the structural "theme" of this folder.]

## 📦 Directory Map & File Manifest
- `[[Link_To_File_1.md]]`: Short, high-level summary of its domain role based on the provided inputs.
- `[[Link_To_File_2.md]]`: Short, high-level summary of its domain role based on the provided inputs.

## ⚠️ Cross-Module Constraints & Rules
- [Identify any recurring patterns, shared dependencies, or strict execution order requirements across these files based on their summaries]
- [e.g., "All files in this directory interface with the thread pool; modifications must respect async bounds."]

### Questions this directory answers:
- [Write a macro-level question about how this entire subsystem fits together]
- [Write an onboarding question that a developer new to this specific directory would ask]\
"""


def summarize_files(
    file_code_pairs: list[tuple[Path, str]],
    repo_root: Path,
    max_workers: int = 8,
    verbose: bool = False,
) -> dict[str, str]:
    """Returns dict mapping repo-relative path strings to LLM-generated file summaries."""
    def explain_one(pair: tuple[Path, str]) -> tuple[str, str]:
        path, code = pair
        rel = path.relative_to(repo_root).as_posix()
        prompt = PROMPT_TEMPLATE.format(
            file_name=path.name,
            file_path=rel,
            code_content=code,
        )
        return rel, _call_claude(prompt)

    total = len(file_code_pairs)
    done = 0
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(explain_one, pair): pair for pair in file_code_pairs}
        for fut in as_completed(futures):
            rel, explanation = fut.result()
            results[rel] = explanation
            if verbose:
                done += 1
                print(f"  [{done}/{total}] {rel}", flush=True)
    return results


def summarize_directories(
    file_summaries: dict[str, str],
    repo_root: Path,
    max_workers: int = 8,
    verbose: bool = False,
) -> dict[str, str]:
    """Returns dict mapping repo-relative directory path strings to LLM-generated MOC summaries."""
    dir_files: dict[str, list[tuple[str, str]]] = {}
    for rel_path, summary in file_summaries.items():
        parent = str(Path(rel_path).parent)
        if parent == ".":
            continue
        dir_files.setdefault(parent, []).append((rel_path, summary))

    def summarize_one(dir_path: str, children: list[tuple[str, str]]) -> tuple[str, str]:
        child_summaries = "\n\n---\n\n".join(
            f"### {Path(p).name}\n{s}" for p, s in sorted(children)
        )
        prompt = AGGREGATE_INDEX_PROMPT.format(
            directory_name=Path(dir_path).name,
            directory_path=dir_path,
            child_summaries=child_summaries,
        )
        return dir_path, _call_claude(prompt)

    total = len(dir_files)
    done = 0
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(summarize_one, dp, children): dp
            for dp, children in dir_files.items()
        }
        for fut in as_completed(futures):
            dir_path, moc = fut.result()
            results[dir_path] = moc
            if verbose:
                done += 1
                print(f"  [{done}/{total}] {dir_path}/", flush=True)
    return results

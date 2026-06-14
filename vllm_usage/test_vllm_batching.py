import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vllm_usage.vllm_batching import VLLMBatching

PROMPT_TEMPLATE = (
    "Explain what the following code does in 2-3 sentences. "
    "Focus on its purpose and key functionality, not line-by-line details.\n\n"
    "```\n{}\n```"
)

SNIPPETS = [
    # snippet 1: simple class
    """\
class FileNode:
    def __init__(self, path, name, imports=None, summary=""):
        self.path = path
        self.name = name
        self.imports = imports or []
        self.summary = summary

    def to_markdown(self):
        links = "\\n".join(f"- [[{p}]]" for p in self.imports)
        return f"## {self.name}\\n\\n{self.summary}\\n\\n{links}"
""",
    # snippet 2: tree walk
    """\
def collect_files(repo_root, ignore_dirs, registry):
    files = []
    for f in sorted(repo_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(repo_root)
        if any(part in ignore_dirs for part in rel.parts):
            continue
        if f.suffix not in registry:
            continue
        files.append(f)
    return files
""",
    # snippet 3: batched generation
    """\
def generate(self, prompts):
    prompts = [self.prompt_template.format(p) for p in prompts]
    outputs = self.llm.generate(prompts, self.sampling_params)
    return [output.outputs[0].text for output in outputs]
""",
]


def test_batching():
    print("Initialising VLLMBatching...")
    batcher = VLLMBatching(prompt_template=PROMPT_TEMPLATE)

    print(f"Running batch of {len(SNIPPETS)} snippets...")
    results = batcher.generate(SNIPPETS)

    for i, (snippet, explanation) in enumerate(zip(SNIPPETS, results), 1):
        print(f"\n--- Snippet {i} ---")
        print(snippet[:120].strip(), "..." if len(snippet) > 120 else "")
        print(f"\nExplanation:\n{explanation.strip()}")

    assert len(results) == len(SNIPPETS), "Expected one result per snippet"
    assert all(isinstance(r, str) and r.strip() for r in results), "Each result should be non-empty text"
    print("\nAll assertions passed.")


if __name__ == "__main__":
    test_batching()

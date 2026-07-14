#!/usr/bin/env python3
"""
UserPromptSubmit hook: when a prompt looks like a codebase-understanding
question (architecture, "how does X work", "where is Y", feature/TODO
questions, implementation planning), inject a reminder to prefer codelore's
MCP tools (and the Obsidian vault) over raw Read/Grep/Bash — falling back to
direct repo exploration only if those tools turn out to be insufficient.

instructions= on the MCP server carries the same guidance, but it's a static
hint competing with everything else in context; this hook re-surfaces it at
the exact moment it matters, keyed off the actual prompt.
"""
import json
import re
import sys

_PATTERNS = [
    r"\bhow (does|do|is|are|did)\b",
    r"\bwhere (is|are|does|can i find)\b",
    r"\bwhat (does|is|are)\b",
    r"\bexplain\b",
    r"\barchitecture\b",
    r"\bunderstand(ing)?\b",
    r"\bimplement(ing|ation)?\b",
    r"\badd (a |an )?(new )?feature\b",
    r"\btodo(s)?\b",
    r"\bwhy (does|is|do|did)\b",
    r"\b(overview|walkthrough) of\b",
    r"\bfind\b.*\b(function|class|usage|reference|definition)\b",
    r"\bwhich file\b",
]
_COMBINED = re.compile("|".join(_PATTERNS), re.IGNORECASE)

_REMINDER = (
    "Reminder: this prompt looks like a codebase-understanding, "
    "\"how/where/why does X work\", or feature/TODO/implementation-planning "
    "question. Start with codelore's MCP tools (search_code, explore_repo, "
    "find_todos) and the Obsidian vault tools — they're scoped to the actual "
    "target repo (see codelore's server instructions / get_active_scope), not "
    "necessarily the current working directory. Only fall back to raw "
    "Read/Grep/Bash on the real repo if the vault/search results turn out to "
    "be insufficient, stale, or the user asks for more current detail than the "
    "vault covers — codelore first, direct exploration as the fallback."
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    prompt = data.get("prompt", "")
    if not prompt or not _COMBINED.search(prompt):
        return
    print(_REMINDER)


if __name__ == "__main__":
    main()

"""src/agent/retriever/agent_retriever.py — locate + load prompt components.

Source of truth: docs/phase-3-workflow.yaml §3 (PromptRetriever contract).

Canonical path + spelling. The previous typo'd module at
`src/agent/retriver/agent_retiver.py` (class `AgentRetiver`) is kept
as a deprecation shim that re-exports `AgentRetriever` from this file.

Pure I/O. Knows nothing about prompt composition, agent behavior, or
LLM. Same `load("resolver_agent")` and `load("retriever_agent")` differ
only in the agent-name string; the agent-specific bits live in each
agent's own `prompts/` directory.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..prompts import AgentPrompts


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class AgentRetriever:
    """Load an agent's prompt components from disk.

    Resolution: <root>/<agent_name>/prompts/<component>[.ext]
    Extensions tried in order: bare, .md, .txt.
    """

    DEFAULT_ROOT: Path = Path("src/agent")
    COMPONENT_NAMES: tuple = ("role", "tools", "few_shot_examples",
                             "state_examples", "guardrails")
    VERSION_FILE_NAMES: tuple = ("VERSION", "META", ".version")

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else self.DEFAULT_ROOT

    # -------- public API --------

    def load(self, agent_name: str) -> AgentPrompts:
        """Return AgentPrompts populated from <root>/<agent_name>/prompts/.

        Missing `prompts/` directory: raises FileNotFoundError.
        Missing individual component files: returned as None.
        """
        prompts_dir = self.root / agent_name / "prompts"
        if not prompts_dir.is_dir():
            raise FileNotFoundError(
                f"No prompts/ directory for agent {agent_name!r}: "
                f"{prompts_dir}. (Did you typo the name?)"
            )
        return AgentPrompts(
            agent_name=agent_name,
            role=self._read(prompts_dir / "role"),
            tools=self._read(prompts_dir / "tools"),
            few_shot_examples=self._read(prompts_dir / "few_shot_examples"),
            state_examples=self._read(prompts_dir / "state_examples"),
            guardrails=self._read(prompts_dir / "guardrails"),
            version=self._read_version(prompts_dir.parent),
            path=prompts_dir,
        )

    def exists(self, agent_name: str) -> bool:
        return (self.root / agent_name / "prompts").is_dir()

    # -------- internals --------

    @staticmethod
    def _read(path: Path) -> Optional[str]:
        """Read a component file; tolerate missing extension variants."""
        candidates = [path]                              # bare
        if path.suffix == "":
            candidates.extend([path.with_suffix(".md"), path.with_suffix(".txt")])
        for c in candidates:
            if c.exists() and c.is_file():
                return c.read_text()
        return None

    @staticmethod
    def _read_version(agent_dir: Path) -> Optional[str]:
        """Read agent_dir/VERSION (preferred) or fallback names."""
        for name in AgentRetriever.VERSION_FILE_NAMES:
            p = agent_dir / name
            if p.exists() and p.is_file():
                return p.read_text().strip()
        return None

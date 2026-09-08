"""src/agent/prompts.py — shared dataclass for loaded prompt components.

Source of truth: docs/phase-3-workflow.yaml §3 (modular prompts).

Why a separate module: both `retriever/agent_retriever.py` (which
constructs) and `prompt_builder.py` (which consumes) need this type.
Putting it in either side creates a circular import.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AgentPrompts:
    """Loaded prompt components for one agent.

    Each field holds the raw text of the corresponding prompts/<component>
    file (`.md` / `.txt` / no-extension all OK). Missing files are
    represented by `None`; the PromptBuilder applies its policy.
    """
    agent_name: str
    role: Optional[str] = None
    tools: Optional[str] = None
    few_shot_examples: Optional[str] = None
    state_examples: Optional[str] = None       # Phase 5E: 5th modular file
    guardrails: Optional[str] = None
    version: Optional[str] = None
    path: Optional[Path] = None          # for debug + log scrapability

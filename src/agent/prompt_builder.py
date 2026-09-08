"""src/agent/prompt_builder.py — compose loaded prompts into a final string.

Source of truth: docs/phase-3-workflow.yaml §3 (PromptBuilder contract).

Pure composition. No I/O. No agent-specific knowledge. One
`build()` method that takes a generic AgentPrompts + optional `extra`
context and emits the string that will go to the LLM as the system
message.

Section order is fixed (cannot be overridden by callers):
  1. role
  2. tools
  3. few_shot_examples
  4. guardrails
  5. extra (optional; runtime context)

If `prompts.version` is set, a one-line HTML comment is prepended so
log-scrapers can identify the prompt version in production.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .prompts import AgentPrompts


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


@dataclass(frozen=True)
class PromptBuilder:
    COMPOSE_POLICY: str = "skip"   # "skip" | "default" | "raise"
    DEFAULT_ROLE_PROMPT: str = (
        "You are an AI assistant. (Default role prompt; replace by writing "
        "the agent's `prompts/role` file.)"
    )
    DEFAULT_GUARDRAILS: str = (
        "No default guardrails; treat all requests at face value. "
        "(Replace by writing the agent's `prompts/guardrails` file.)"
    )
    SECTION_ORDER: tuple = (
        "role", "tools", "few_shot_examples", "state_examples", "guardrails",
    )
    EXTRA_HEADER: str = "## Runtime Context"

    # SECTION_HEADERS is instance-level (default_factory) so a frozen dataclass
    # accepts it. Use the class-level attribute via property to keep call sites
    # free of `PromptBuilder().SECTION_HEADERS` (it's the same object either way).
    @property
    def SECTION_HEADERS(self) -> dict:
        return {
            "role":              "## Your Role",
            "tools":             "## How You Use Your Tools",
            "few_shot_examples": "## Examples",
            "state_examples":    "## Routing decisions (deep dive)",
            "guardrails":        "## Guardrails",
        }

    def build(
        self,
        prompts: AgentPrompts,
        *,
        extra: Optional[str] = None,
        extra_header: Optional[str] = None,
    ) -> str:
        """Compose the four components + optional `extra` block.

        Returns a single string intended to be the LLM `system` message.
        """
        sections: list[str] = []
        for name in self.SECTION_ORDER:
            content = getattr(prompts, name, None)
            if content is None or not content.strip():
                if name == "guardrails" and self.COMPOSE_POLICY != "raise":
                    log.warning(
                        "agent=%s: guardrails prompt missing — agent has no enforced constraints",
                        prompts.agent_name,
                    )
                if self.COMPOSE_POLICY == "raise":
                    raise ValueError(
                        f"agent={prompts.agent_name}: missing required component {name!r}"
                    )
                if self.COMPOSE_POLICY == "default":
                    if name == "role":
                        content = self.DEFAULT_ROLE_PROMPT
                    elif name == "guardrails":
                        content = self.DEFAULT_GUARDRAILS
                else:                                # "skip"
                    continue
            if content is None or not content.strip():
                # Section was missing + policy != "raise": continue.
                continue
            sections.append(f"{self.SECTION_HEADERS[name]}\n{content.strip()}")

        if extra is not None and extra.strip():
            header = extra_header if extra_header is not None else self.EXTRA_HEADER
            sections.append(f"{header}\n{extra.strip()}")

        body = "\n\n".join(sections)
        if prompts.version:
            body = (
                f"<!-- agent={prompts.agent_name} "
                f"version={prompts.version} -->\n" + body
            )
        return body

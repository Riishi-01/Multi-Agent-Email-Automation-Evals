"""tests/test_prompt_builder.py — unit tests for the prompt assembler (pure)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agent.prompts import AgentPrompts
from src.agent.prompt_builder import PromptBuilder


def _prompts(*, role=None, tools=None, ex=None, guard=None, ver=None):
    return AgentPrompts(
        agent_name="t", role=role, tools=tools, few_shot_examples=ex,
        guardrails=guard, version=ver,
    )


class AllFourPresent(unittest.TestCase):
    def test_section_order(self):
        b = PromptBuilder()
        # No version header for this test (no `ver` arg).
        text = b.build(_prompts(role="R", tools="T", ex="E", guard="G"))
        idx_r = text.find("## Your Role")
        idx_t = text.find("## How You Use Your Tools")
        idx_e = text.find("## Examples")
        idx_g = text.find("## Guardrails")
        self.assertEqual(idx_r, 0)                                # role is first (no version comment here)
        self.assertTrue(idx_r < idx_t < idx_e < idx_g)

    def test_version_header(self):
        b = PromptBuilder()
        text = b.build(_prompts(role="R", tools="T", ex="E", guard="G", ver="2026.09.04"))
        self.assertTrue(text.startswith("<!-- agent=t version=2026.09.04 -->"))

    def test_no_version_header_when_absent(self):
        b = PromptBuilder()
        text = b.build(_prompts(role="R"))
        self.assertFalse(text.startswith("<!--"))

    def test_extra_section_at_end(self):
        b = PromptBuilder()
        text = b.build(_prompts(role="R"), extra="runtime context here")
        self.assertTrue(text.endswith("runtime context here"))
        self.assertIn("## Runtime Context", text)

    def test_extra_custom_header(self):
        b = PromptBuilder()
        text = b.build(_prompts(role="R"),
                       extra="...", extra_header="## Live Facts")
        self.assertIn("## Live Facts", text)
        self.assertNotIn("## Runtime Context", text)

    def test_empty_extra_omitted(self):
        b = PromptBuilder()
        text = b.build(_prompts(role="R"), extra="   \n  \n  ")
        self.assertNotIn("## Runtime Context", text)

    def test_strip_whitespace_in_each_section(self):
        b = PromptBuilder()
        text = b.build(_prompts(role="  \n   role body  \n   "))
        # No leading/trailing whitespace inside the section.
        idx = text.find("## Your Role") + len("## Your Role")
        section = text[idx:].lstrip("\n").split("\n\n")[0]
        self.assertFalse(section.startswith(" "))
        self.assertFalse(section.endswith(" "))


class MissingComponents(unittest.TestCase):
    def test_default_skip_policy(self):
        b = PromptBuilder()                                      # COMPOSE_POLICY="skip"
        text = b.build(_prompts(role="R"))                      # tools / ex / guard missing
        self.assertIn("## Your Role", text)
        self.assertNotIn("## How You Use Your Tools", text)
        self.assertNotIn("## Examples", text)
        self.assertNotIn("## Guardrails", text)

    def test_guardrails_missing_logs_warning(self):
        b = PromptBuilder()
        with self.assertLogs("src.agent.prompt_builder", level="WARNING") as cm:
            b.build(_prompts(role="R", tools="T", ex="E", guard=None))
        # Verify at least one log line mentions guardrails.
        self.assertTrue(any("guardrails" in line.lower() for line in cm.output))

    def test_default_policy_fills_role(self):
        # COMPOSE_POLICY="default" → DEFAULT_ROLE_PROMPT fills missing role
        b = PromptBuilder(COMPOSE_POLICY="default")
        text = b.build(_prompts(tools="T", ex="E", guard="G"))   # role missing
        self.assertIn("## Your Role", text)
        self.assertIn("Default role prompt", text)

    def test_raise_policy_strict(self):
        # COMPOSE_POLICY="raise" → missing component is a hard error
        b = PromptBuilder(COMPOSE_POLICY="raise")
        with self.assertRaises(ValueError):
            b.build(_prompts(tools="T"))                          # role missing → raise


class Determinism(unittest.TestCase):
    def test_same_inputs_same_output(self):
        b = PromptBuilder()
        inputs = dict(role="R", tools="T", ex="E", guard="G", ver="v1")
        a = b.build(_prompts(**inputs))
        c = b.build(_prompts(**inputs))
        self.assertEqual(a, c)


if __name__ == "__main__":
    unittest.main(verbosity=2)

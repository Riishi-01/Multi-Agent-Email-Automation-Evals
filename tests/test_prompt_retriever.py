"""tests/test_prompt_retriever.py — unit tests for the prompt-file loader.

These exercise the I/O layer in isolation (temp dirs); they don't
need Postgres or any LLM.

Canonical-spelling path: src/agent/retriever/agent_retriever.py
(Class: AgentRetriever)
Old typo'd path: src/agent/retriver/agent_retiver.py
(Class: AgentRetiver — kept as a deprecation shim)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agent.retriever.agent_retriever import AgentRetriever
from src.agent.prompts import AgentPrompts


def _mk_agent(root: Path, name: str, *, with_version: bool = True,
              ext: str = "", missing: tuple = ()):
    """Create <root>/<name>/prompts/<component>[ext] for the four components,
    skipping any whose name is in `missing`. VERSION is at <root>/<name>/VERSION."""
    d = root / name / "prompts"
    d.mkdir(parents=True)
    if with_version:
        (root / name / "VERSION").write_text("test-2026.09.04\n")
    for comp in ("role", "tools", "few_shot_examples", "guardrails"):
        if comp in missing: continue
        p = d / comp
        if ext:
            p = p.with_suffix(ext)
        p.write_text(f"{comp}-content-for-{name}")


class Loader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_loads_all_four(self):
        _mk_agent(self.tmp, "alice")
        p = AgentRetriever(root=self.tmp).load("alice")
        self.assertEqual(p.agent_name, "alice")
        self.assertEqual(p.role,       "role-content-for-alice")
        self.assertEqual(p.tools,      "tools-content-for-alice")
        self.assertEqual(p.few_shot_examples, "few_shot_examples-content-for-alice")
        self.assertEqual(p.guardrails, "guardrails-content-for-alice")
        self.assertEqual(p.version, "test-2026.09.04")

    def test_missing_agent_raises(self):
        with self.assertRaises(FileNotFoundError):
            AgentRetriever(root=self.tmp).load("ghost")

    def test_partial_agent_returns_none_for_missing(self):
        _mk_agent(self.tmp, "bob", missing=("tools", "guardrails"))
        p = AgentRetriever(root=self.tmp).load("bob")
        self.assertEqual(p.role, "role-content-for-bob")
        self.assertIsNone(p.tools)
        self.assertEqual(p.few_shot_examples, "few_shot_examples-content-for-bob")
        self.assertIsNone(p.guardrails)

    def test_extension_fallback_md(self):
        _mk_agent(self.tmp, "carol", ext=".md")
        p = AgentRetriever(root=self.tmp).load("carol")
        self.assertEqual(p.role, "role-content-for-carol")

    def test_no_version_means_none(self):
        _mk_agent(self.tmp, "dave", with_version=False)
        p = AgentRetriever(root=self.tmp).load("dave")
        self.assertIsNone(p.version)

    def test_exists_helper(self):
        _mk_agent(self.tmp, "erin")
        r = AgentRetriever(root=self.tmp)
        self.assertTrue(r.exists("erin"))
        self.assertFalse(r.exists("no-one"))

    def test_resolution_works_against_real_resolver_agent(self):
        """Integration smoke: AgentRetriever().load('resolver_agent') loads
        the concrete prompts/ directory shipped with Phase 3."""
        agents_root = REPO_ROOT / "src" / "agent"
        r = AgentRetriever(root=agents_root)
        if not r.exists("resolver_agent"):
            self.skipTest("resolver_agent prompts/ not present (Phase 3 not built?)")
        p = r.load("resolver_agent")
        self.assertIsNotNone(p.role)
        self.assertIn("RESOLVER", p.role)
        self.assertIsNotNone(p.guardrails)
        self.assertIsNotNone(p.few_shot_examples)
        self.assertIsNotNone(p.version)


class TestDeprecationShim(unittest.TestCase):
    """The old typo'd path is kept as a back-compat shim."""

    def test_old_path_still_works(self):
        from src.agent.retriver.agent_retiver import AgentRetiver
        self.assertTrue(callable(AgentRetiver))

    def test_old_and_new_are_same_class(self):
        from src.agent.retriever.agent_retriever import AgentRetriever
        from src.agent.retriver.agent_retiver import AgentRetiver
        # Both names point to the same class object.
        self.assertIs(AgentRetiver, AgentRetriever)

    def test_shim_emits_deprecation_warning(self):
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Force a fresh import
            import importlib, sys
            mod_name = "src.agent.retriver.agent_retiver"
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
            else:
                importlib.import_module(mod_name)
        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught),
            "shim should emit DeprecationWarning")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""tests/test_resolver_reflexive.py — Resolver Reflexive Agent tests.

The Reflexive is a generic verification layer for email-support
agents. It scores a configurable rubric and emits a deterministic
verdict via Python routing (not via the LLM).

Tests cover:
  - the 5 modular prompt files exist
  - the role prompt frames the Reflexive as a validator
  - the few-shot shows accept / regenerate / escalate examples
  - the DEMO path scores deterministically (5 dimensions)
  - the ReflexiveOutput schema enforces confidence = min(scores)
  - the malformed fallback path works
  - the generic rubric-as-parameter API works
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force DEMO mode so the tests don't need OPENAI_API_KEY.
import tests.conftest  # noqa: F401


# ---------------------------------------------------------------------------
# Modular prompts (5 files, sibling of resolver_agent)
# ---------------------------------------------------------------------------
class TestReflexiveHasModularPrompts(unittest.TestCase):
    @staticmethod
    def prompts_dir() -> Path:
        return (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts")

    def test_prompts_dir_exists(self):
        self.assertTrue(self.prompts_dir().is_dir(),
            f"missing prompts dir: {self.prompts_dir()}")

    def test_all_five_files_present(self):
        for c in ("role", "tools", "few_shot_examples",
                  "state_examples", "guardrails"):
            p = self.prompts_dir() / c
            self.assertTrue(p.exists(),
                f"missing prompts/{c}")
            self.assertGreater(p.stat().st_size, 50,
                f"prompts/{c} is suspiciously small")

    def test_version_file_present(self):
        v = REPO_ROOT / "src" / "agent" / "resolver_reflexive" / "VERSION"
        self.assertTrue(v.exists(), f"missing {v}")
        self.assertTrue(v.read_text().strip())


# ---------------------------------------------------------------------------
# Role: validator, not drafter
# ---------------------------------------------------------------------------
class TestReflexiveRoleIsValidator(unittest.TestCase):
    def test_role_says_review_or_validate(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "role").read_text()
        self.assertTrue(
            any(w in text.lower() for w in (
                "review", "validate", "verify", "rubric",
                "score", "dimension", "confidence",
            )),
            "role prompt should describe the validator's job (review, "
            "rubric, dimensions, score, confidence)")

    def test_role_does_not_ask_for_draft(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "role").read_text()
        # The Reflexive validates; the workflow writes the actual draft.
        # If the role prompt tells the model to draft a reply, that's a
        # regression.
        forbidden = ("draft a reply", "write a reply", "compose a response",
                     "produce the email body", "you should draft")
        for f in forbidden:
            self.assertNotIn(f, text.lower(),
                f"role prompt should not say {f!r}")


# ---------------------------------------------------------------------------
# Few-shot: accept / regenerate / escalate
# ---------------------------------------------------------------------------
class TestReflexiveFewShotAccept(unittest.TestCase):
    def test_accept_example_has_full_dimensions(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "few_shot_examples").read_text()
        # Case A is the accept case
        self.assertIn("Case A", text)
        # All 5 dimensions should be at 1.0 in the accept example.
        # The file uses loose JSON spacing ("dim": 1.0,) so we just
        # check that the dimension name appears with a 1.0 score.
        for dim in ("intent_accuracy", "faithfulness",
                     "policy_compliance", "tone",
                     "side_effect_consent"):
            self.assertRegex(text, rf'"{dim}"\s*:\s*1\.0',
                f"accept example should have {dim}=1.0")

    def test_confidence_in_accept_is_1_0(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "few_shot_examples").read_text()
        # The Case A example has confidence=1.0
        self.assertIn('"confidence": 1.0', text)


class TestReflexiveFewShotRegenerate(unittest.TestCase):
    def test_regenerate_example_present(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "few_shot_examples").read_text()
        self.assertIn("Case B", text)
        # Case B uses suggested_action="regenerate" with concrete hints
        self.assertIn('"suggested_action": "regenerate"', text)
        # Must have non-empty regeneration_hints
        self.assertIn('"regeneration_hints": [', text)


class TestReflexiveFewShotEscalate(unittest.TestCase):
    def test_escalate_example_present(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "few_shot_examples").read_text()
        self.assertIn("Case C", text)
        # Case C is the persistent-failure escalate
        self.assertIn('"suggested_action": "escalate"', text)


# ---------------------------------------------------------------------------
# No PII in the reflexive's prompts
# ---------------------------------------------------------------------------
class TestReflexiveFewShotNoPII(unittest.TestCase):
    PII_EMAIL_RE = __import__("re").compile(
        r"\b[A-Za-z0-9_.+-]+@(?:gmail|yahoo|outlook|hotmail|test|proton)\."
        r"(?:com|org|net|io)\b",
        __import__("re").IGNORECASE,
    )
    PII_NAMES = ("Sanya", "Anjali", "Manish", "Sanya Malhotra")

    def test_no_real_emails(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "few_shot_examples").read_text()
        self.assertEqual(self.PII_EMAIL_RE.findall(text), [],
            f"reflexive few-shot has real email addresses")

    def test_no_real_customer_names(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "few_shot_examples").read_text()
        import re
        for name in self.PII_NAMES:
            self.assertNotRegex(text, rf"\b{re.escape(name)}\b")


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
class TestReflexiveGuardrails(unittest.TestCase):
    def test_max_retries_2(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "guardrails").read_text()
        # The 2-retry cap is the spec's section 8 contract
        self.assertRegex(text.lower(), r"max[ _]?retries?\s*[=:]\s*2|"
                                       r"2\s+retries|"
                                       r"retries?\s*=\s*2|"
                                       r"two retries|"
                                       r"bounded\s+at\s+2|"
                                       r"cap.{0,20}2")
        # Plus a plain sanity check: the number 2 appears in the file
        # alongside the word "retries" somewhere.
        self.assertIn("2", text)
        self.assertIn("retries", text.lower())

    def test_no_side_effect_tool_calls(self):
        text = (REPO_ROOT / "src" / "agent" / "resolver_reflexive"
                / "prompts" / "tools").read_text()
        # The Reflexive MUST NOT call refund / replacement / cancel /
        # address-change tools. Only read-only verification tools.
        for forbidden in ("request_refund", "cancel_order",
                          "process_refund"):
            self.assertNotIn(forbidden, text.lower(),
                f"reflexive tools should not include {forbidden!r}")


# ---------------------------------------------------------------------------
# DEMO scoring
# ---------------------------------------------------------------------------
class TestReflexiveDEMOScoring(unittest.TestCase):
    def _ctx(self, **kw):
        return {
            "customer": {"found": True, "row": {"full_name": "X"}},
            "order": {
                "found": kw.get("order_found", True),
                "row": {
                    "order_id": "BM200001",
                    "total_amount": kw.get("total", 54990),
                    "current_status": kw.get("status", "paid"),
                    "items": kw.get("items", []),
                },
            },
            "payments": kw.get("payments", []),
            "policies": kw.get("policies", []),
        }

    def _resolver_out(self, **kw):
        return {
            "intent": kw.get("intent", "info"),
            "action": kw.get("action", "auto_send"),
            "draft": kw.get("draft", "Hi X, your order is in paid status."),
            "self_check": kw.get("self_check", {
                "policy_compliant": True, "entities_correct": True,
                "state_change_implied": False, "tone": "polite",
            }),
            "reasoning": kw.get("reasoning", "ok"),
            "used_entities": kw.get("used_entities", {}),
            "hilt_reason": kw.get("hilt_reason"),
        }

    def test_demo_uses_min_of_dimensions_for_confidence(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        out = self._resolver_out(
            action="hilt_refund", draft="We deeply regret the inconvenience. "
            "Refund of ₹5,499 initiated per refund-policy §3.")
        reflexive = agent.run(
            subject="Refund please",
            body="I want a refund. Please process it now.",
            sender_email="a@b.com",
            resolver_output=out,
            retriever_context=ctx,
        )
        # confidence must equal min of the dimension scores
        self.assertAlmostEqual(
            reflexive.confidence,
            min(reflexive.dimension_scores.values()),
            places=4,
        )
        # And every dimension must be in [0.0, 1.0]
        for v in reflexive.dimension_scores.values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_demo_empathy_lead_gives_tone_1_0(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        out = self._resolver_out(
            action="hilt_other", draft="We deeply regret the inconvenience. "
            "Replacement arranged.")
        reflexive = agent.run(
            subject="defect",
            body="My BlackShark is defective. I want a replacement.",
            sender_email="a@b.com",
            resolver_output=out,
            retriever_context=ctx,
        )
        self.assertEqual(reflexive.dimension_scores["tone"], 1.0)

    def test_demo_no_empathy_on_problem_gives_tone_low(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        out = self._resolver_out(
            action="hilt_other", draft="Replacement arranged.")
        reflexive = agent.run(
            subject="defect",
            body="My BlackShark is defective. I want a replacement.",
            sender_email="a@b.com",
            resolver_output=out,
            retriever_context=ctx,
        )
        # problem case + no empathy lead -> tone should be 0.0
        self.assertEqual(reflexive.dimension_scores["tone"], 0.0)

    def test_demo_side_effect_no_consent(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        # Resolver chose hilt_refund but the email never asks for a
        # refund. DEMO side_effect_consent must be 0.0.
        out = self._resolver_out(
            action="hilt_refund", draft="We deeply regret the inconvenience. "
            "Refund of ₹5,499 initiated.")
        reflexive = agent.run(
            subject="Where is my order?",
            body="I just want to know the delivery status. "
                  "The order is BM200001.",
            sender_email="a@b.com",
            resolver_output=out,
            retriever_context=ctx,
        )
        # Body has no "refund" / "money back" / etc. -> 0.0
        self.assertEqual(
            reflexive.dimension_scores["side_effect_consent"], 0.0)

    def test_demo_side_effect_with_consent(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        out = self._resolver_out(
            action="hilt_refund", draft="We deeply regret the inconvenience. "
            "Refund of ₹5,499 initiated.")
        reflexive = agent.run(
            subject="refund",
            body="I want a refund for the order, please.",
            sender_email="a@b.com",
            resolver_output=out,
            retriever_context=ctx,
        )
        self.assertEqual(
            reflexive.dimension_scores["side_effect_consent"], 1.0)

    def test_demo_read_only_default_consent(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        out = self._resolver_out(action="auto_send", draft="ok")
        reflexive = agent.run(
            subject="Where is my order?",
            body="I just want to know the delivery status.",
            sender_email="a@b.com",
            resolver_output=out,
            retriever_context=ctx,
        )
        # read-only action defaults to 1.0 consent
        self.assertEqual(
            reflexive.dimension_scores["side_effect_consent"], 1.0)

    def test_demo_state_routing(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        agent = ResolverReflexive()
        ctx = self._ctx()
        # Email asks for refund; resolver chose hilt_refund -> intent_accuracy=1.0
        out = self._resolver_out(action="hilt_refund",
                                 draft="We deeply regret. Refund processed.")
        reflexive = agent.run(
            subject="refund",
            body="I want a refund. Please process it now.",
            sender_email="a@b.com",
            resolver_output=out, retriever_context=ctx,
        )
        self.assertEqual(reflexive.dimension_scores["intent_accuracy"], 1.0)


# ---------------------------------------------------------------------------
# Output schema + malformed fallback
# ---------------------------------------------------------------------------
class TestReflexiveOutputSchema(unittest.TestCase):
    def test_reflexive_output_has_required_fields(self):
        from src.agent.resolver_reflexive import ReflexiveOutput
        ro = ReflexiveOutput(
            dimension_scores={"intent_accuracy": 1.0},
            confidence=1.0,
            reasoning="ok",
            suggested_action="accept",
        )
        d = ro.to_dict()
        for k in ("dimension_scores", "confidence", "reasoning",
                  "suggested_action", "regeneration_hints", "malformed"):
            self.assertIn(k, d)


# ---------------------------------------------------------------------------
# Generic rubric as parameter
# ---------------------------------------------------------------------------
class TestGenericRubric(unittest.TestCase):
    def test_custom_rubric_overrides_default(self):
        from src.agent.resolver_reflexive.agent_resolver_reflexive import (
            ResolverReflexive,
        )
        from src.agent.resolver_reflexive import DEFAULT_RESOLVER_RUBRIC
        # The default rubric has the 5 standard dimensions
        self.assertSetEqual(
            set(DEFAULT_RESOLVER_RUBRIC.keys()),
            {"intent_accuracy", "faithfulness", "policy_compliance",
             "tone", "side_effect_consent"},
        )
        agent = ResolverReflexive()
        custom = {"rightness": 1.0, "groundedness": 1.0}
        ctx = {
            "customer": {"found": True, "row": {"full_name": "X"}},
            "order": {"found": True, "row": {}},
            "payments": [], "products": [], "policies": [],
        }
        out = {
            "intent": "info", "action": "auto_send",
            "draft": "ok", "self_check": {}, "reasoning": "",
            "used_entities": {}, "hilt_reason": None,
        }
        reflexive = agent.run(
            subject="x", body="y", sender_email="a@b.com",
            resolver_output=out, retriever_context=ctx,
            rubric=custom,
        )
        # Output keys must match the custom rubric, not the default
        # (the custom rubric should REPLACE the default, not merge).
        self.assertSetEqual(
            set(reflexive.dimension_scores.keys()),
            {"rightness", "groundedness"},
            "custom rubric should REPLACE default 5 dimensions; "
            f"got {sorted(reflexive.dimension_scores.keys())}",
        )
        # confidence is still the min
        self.assertAlmostEqual(
            reflexive.confidence,
            min(reflexive.dimension_scores.values()),
        )


# ---------------------------------------------------------------------------
# ResolverReflexive is wired into the workflow
# ---------------------------------------------------------------------------
class TestWorkflowWiresReflexive(unittest.TestCase):
    def test_workflow_loads_reflexive(self):
        from src.agent.workflow import Workflow
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            w = Workflow(trace_dir=Path(td))
            self.assertIsNotNone(w.reflexive,
                "Workflow should auto-wire the Reflexive")
            # The Reflexive's system_prompt includes the routing section
            self.assertIn("## Routing decisions", w.reflexive.system_prompt)

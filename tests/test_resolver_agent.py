"""tests/test_resolver_agent.py — AM-002 behavior tests (Phase 4C schema).

DEMO mode + validator + lifecycle. Schema-1.1 expectations:
  - 4 decisions: auto_send | hilt_refund | hilt_other | escalate
  - 7-field hilt_reason object on hilt_* actions
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class AgentLifecycle(unittest.TestCase):
    def test_constructor_loads_prompts_once(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        a1 = ResolverAgent()
        a2 = ResolverAgent()
        self.assertEqual(a1.system_prompt, a2.system_prompt)
        self.assertGreater(len(a1.system_prompt), 200)
        self.assertIn("RESOLVER", a1.system_prompt)


class DemoRun(unittest.TestCase):
    def setUp(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        self.agent = ResolverAgent()

    def test_info_auto_send(self):
        out = self.agent.run(
            subject="Order inquiry",
            body="I want to know the expected delivery date for my PS5.",
            sender_email="sanya.delhi@gmail.com",
            context={"customer": {"found": True, "row": {"full_name": "Sanya"}},
                     "order": {"found": True, "row": {"order_id": "BM200001"}},
                     "payments": [], "products": [], "policies": [],
                     "_sender_email": "sanya.delhi@gmail.com"},
        )
        self.assertEqual(out.action, "auto_send")
        self.assertIsNotNone(out.draft)
        self.assertNotIn("[PENDING APPROVAL]", out.draft)
        self.assertIsNone(out.hilt_reason)

    def test_address_change_hilt_other(self):
        out = self.agent.run(
            subject="Address",
            body="I want to change the delivery address for #BM183923 to Mumbai.",
            sender_email="sneha.mumbai86@gmail.com",
            context={"customer": {"found": True, "row": {"full_name": "Sneha"}},
                     "order": {"found": True, "row": {"order_id": "BM183923"}},
                     "payments": [], "products": [], "policies": [],
                     "_sender_email": "sneha.mumbai86@gmail.com"},
        )
        self.assertEqual(out.action, "hilt_other")
        self.assertIsNotNone(out.hilt_reason)
        self.assertIn("[PENDING APPROVAL]", out.draft)

    def test_refund_request_hilt_refund(self):
        out = self.agent.run(
            subject="Damaged PS5",
            body="My PS5 arrived damaged. I want a refund.",
            sender_email="rishit1@gmail.com",
            context={"customer": {"found": True, "row": {"full_name": "Rishit"}},
                     "order": {"found": True, "row": {"order_id": "BM728349"}},
                     "payments": [], "products": [], "policies": [],
                     "_sender_email": "rishit1@gmail.com"},
        )
        self.assertEqual(out.action, "hilt_refund")
        self.assertIsNotNone(out.hilt_reason)
        self.assertIn("[PENDING APPROVAL]", out.draft)

    def test_escalate_when_no_identity(self):
        out = self.agent.run(
            subject="Erasure",
            body="Erase my data under DPDPA.",
            sender_email="",
            context={"customer": {"found": False, "row": None},
                     "order": {"found": False, "row": None},
                     "payments": [], "products": [], "policies": [],
                     "_sender_email": ""},
        )
        self.assertEqual(out.action, "escalate")
        self.assertIsNone(out.draft)
        self.assertIsNone(out.hilt_reason)


class ValidatorUnittest(unittest.TestCase):
    def test_validate_hilt_missing_placeholder(self):
        from src.agent.resolver_agent.agent_resolver import _validate
        ok, err = _validate({
            "action": "hilt_refund",
            "intent": "refund",
            "draft": "no placeholder here",
            "self_check": {"policy_compliant": True, "entities_correct": True,
                           "state_change_implied": True, "tone": "neutral"},
            "hilt_reason": {"policy_clause": "x", "reason_short": "y",
                            "reason_long": "y", "recommended_action": "x",
                            "action_target": "x", "urgency": "low",
                            "human_skill_required": "tier_1"},
        })
        self.assertFalse(ok)
        self.assertIn("[PENDING APPROVAL]", err)

    def test_validate_escalate_requires_null_draft(self):
        from src.agent.resolver_agent.agent_resolver import _validate
        ok, err = _validate({
            "action": "escalate",
            "intent": "escalation",
            "draft": "should be null",
            "self_check": {"policy_compliant": True, "entities_correct": True,
                           "state_change_implied": False, "tone": "neutral"},
        })
        self.assertFalse(ok)
        self.assertIn("null draft", err)

    def test_validate_hilt_other_requires_hilt_reason(self):
        from src.agent.resolver_agent.agent_resolver import _validate
        ok, err = _validate({
            "action": "hilt_other",
            "intent": "logistic",
            "draft": "[PENDING APPROVAL] change address",
            "self_check": {"policy_compliant": True, "entities_correct": True,
                           "state_change_implied": True, "tone": "neutral"},
        })
        self.assertFalse(ok)
        self.assertIn("hilt_reason", err)


class ToDict(unittest.TestCase):
    def test_to_dict_round_trip(self):
        from src.agent.resolver_agent.agent_resolver import ResolverResult
        r = ResolverResult(
            intent="info", action="auto_send", draft="Hi",
            self_check={"tone": "polite"}, reasoning="...",
            used_entities={"order_id": "BM1"}, hilt_reason=None,
        )
        d = r.to_dict()
        for k in ("intent", "action", "draft", "self_check",
                  "reasoning", "used_entities", "hilt_reason"):
            self.assertIn(k, d)


class TestDeadCodeRemoved(unittest.TestCase):
    """The Phase-5 audit removed dead code; these tests guard against
    accidental re-introduction."""

    def test_hilt_triggers_dict_removed(self):
        import src.agent.resolver_agent.agent_resolver as r
        self.assertFalse(
            hasattr(r, "HILT_TRIGGERS"),
            "HILT_TRIGGERS dict is dead code; was removed in Phase 5 audit"
        )

    def test_auto_send_triggers_dict_removed(self):
        import src.agent.resolver_agent.agent_resolver as r
        self.assertFalse(
            hasattr(r, "AUTO_SEND_TRIGGERS"),
            "AUTO_SEND_TRIGGERS dict is dead code; was removed in Phase 5 audit"
        )

    def test_tok_count_removed(self):
        import src.rag as r
        self.assertFalse(
            hasattr(r, "_tok_count"),
            "_tok_count is dead code; was removed in Phase 5 audit"
        )

    def test_normalize_single_dropped_key_col(self):
        import inspect
        from src.tools import _normalize_single
        sig = inspect.signature(_normalize_single)
        self.assertEqual(
            list(sig.parameters.keys()), ["rows"],
            "_normalize_single's key_col param was unused; should be removed"
        )


# ---------------------------------------------------------------------------
# Phase 5D: resolver few-shot — 4 grounded cases + tone/behavior
# ---------------------------------------------------------------------------
class _FewShotFile:
    @staticmethod
    def path() -> Path:
        return (Path(__file__).resolve().parent.parent
                / "src" / "agent" / "resolver_agent" / "prompts"
                / "few_shot_examples")

    @classmethod
    def text(cls) -> str:
        return cls.path().read_text()


class TestResolverFewShotNoPII(unittest.TestCase):
    PII_EMAIL_RE = re.compile(
        r"\b[A-Za-z0-9_.+-]+@(?:gmail|yahoo|outlook|hotmail|test|proton)\."
        r"(?:com|org|net|io)\b",
        re.IGNORECASE,
    )
    PII_NAME_WORDS = ("Sanya", "Anjali", "Manish", "Neha", "Rishit",
                      "Priya", "Vikram", "Divya", "Karan", "Rohit",
                      "Sanya Malhotra")

    def test_no_real_email_addresses(self):
        text = _FewShotFile.text()
        matches = self.PII_EMAIL_RE.findall(text)
        self.assertEqual(matches, [],
            f"few_shot_examples contains real email addresses: {matches}")

    def test_no_real_customer_names(self):
        text = _FewShotFile.text()
        for name in self.PII_NAME_WORDS:
            self.assertNotRegex(text, rf"\b{re.escape(name)}\b",
                f"few_shot_examples contains real customer name: {name!r}")


class TestResolverFewShotHasFourExamples(unittest.TestCase):
    def test_exactly_four_worked_examples(self):
        """The few-shot must contain exactly 4 worked examples."""
        text = _FewShotFile.text()
        # Count "## Example N" headers (or "## Case N")
        headers = re.findall(r"^## (?:Example|Case)\s+\d+", text, re.MULTILINE)
        self.assertEqual(len(headers), 4,
            f"expected 4 worked examples; got {len(headers)}: {headers}")


class TestResolverFewShotFullResponseShape(unittest.TestCase):
    """Each example must show the FULL ResolverResult JSON shape with
    all 7 fields: intent, action, draft, self_check, reasoning,
    used_entities, hilt_reason (null when not hilt)."""

    REQUIRED_FIELDS = (
        '"intent"', '"action"', '"draft"', '"self_check"',
        '"reasoning"', '"used_entities"', '"hilt_reason"',
    )

    def test_every_example_has_all_seven_fields(self):
        text = _FewShotFile.text()
        # Split on example headers
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)[1:]
        self.assertGreaterEqual(len(blocks), 4,
            "expected at least 4 example blocks")
        for i, blk in enumerate(blocks, start=1):
            for field in self.REQUIRED_FIELDS:
                self.assertIn(field, blk,
                    f"Example {i} missing field {field}")


class TestResolverFewShotCoTReasoning(unittest.TestCase):
    def test_every_reasoning_block_has_read_from_retriever(self):
        text = _FewShotFile.text()
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)[1:]
        for i, blk in enumerate(blocks, start=1):
            self.assertIn("Read from retriever", blk,
                f"Example {i} reasoning must start with 'Read from retriever:'")

    def test_every_reasoning_block_has_did_not(self):
        text = _FewShotFile.text()
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)[1:]
        for i, blk in enumerate(blocks, start=1):
            self.assertIn("Did NOT", blk,
                f"Example {i} reasoning must enumerate 'Did NOT:' items")


class TestResolverFewShotProblemCaseHasEmpathy(unittest.TestCase):
    """Cases 1 and 2 are problem cases (defective, duplicate charge).
    Their drafts must open with an empathy phrase."""

    EMPATHY_PHRASES = [
        "We deeply regret the inconvenience",
        "We sincerely apologize",
        "We apologize",
        "We're sorry",
    ]

    def _block(self, text: str, example_num: int) -> str:
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)
        # blocks[0] is preamble, blocks[1] is Example 1
        return blocks[example_num]

    def test_case_1_problem_opens_with_empathy(self):
        text = _FewShotFile.text()
        b = self._block(text, 1)
        # Find the "draft" value (between "draft": " and the next unescaped ")
        m = re.search(r'"draft"\s*:\s*"([^"]+)"', b)
        self.assertIsNotNone(m, "Case 1 has no draft field")
        draft = m.group(1)
        self.assertTrue(
            any(p in draft for p in self.EMPATHY_PHRASES),
            f"Case 1 (problem) draft does not open with empathy: {draft!r}",
        )

    def test_case_2_problem_opens_with_empathy(self):
        text = _FewShotFile.text()
        b = self._block(text, 2)
        m = re.search(r'"draft"\s*:\s*"([^"]+)"', b)
        self.assertIsNotNone(m, "Case 2 has no draft field")
        draft = m.group(1)
        self.assertTrue(
            any(p in draft for p in self.EMPATHY_PHRASES),
            f"Case 2 (problem) draft does not open with empathy: {draft!r}",
        )


class TestResolverFewShotPlainCaseNoEmpathy(unittest.TestCase):
    """Cases 3 and 4 are plain-question / stock-inquiry cases.
    Their drafts must NOT open with empathy."""

    def _block(self, text: str, example_num: int) -> str:
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)
        return blocks[example_num]

    def _draft(self, block: str) -> str:
        m = re.search(r'"draft"\s*:\s*"([^"]+)"', block)
        return m.group(1) if m else ""

    EMPATHY_PHRASES = [
        "We deeply regret the inconvenience",
        "We sincerely apologize",
        "We apologize",
        "We're sorry",
    ]

    def test_case_3_plain_does_not_open_with_empathy(self):
        text = _FewShotFile.text()
        draft = self._draft(self._block(text, 3))
        self.assertNotIn("We deeply regret the inconvenience", draft)
        for p in self.EMPATHY_PHRASES:
            self.assertNotIn(p, draft,
                f"Case 3 (plain question) draft opens with empathy {p!r}: {draft!r}")

    def test_case_4_stock_does_not_open_with_empathy(self):
        text = _FewShotFile.text()
        draft = self._draft(self._block(text, 4))
        for p in self.EMPATHY_PHRASES:
            self.assertNotIn(p, draft,
                f"Case 4 (stock) draft opens with empathy {p!r}: {draft!r}")


class TestResolverFewShotNoStockNumber(unittest.TestCase):
    """Neither example should quote a specific stock count."""

    STOCK_NUMBERS = (
        r"\bstock\s*(?:is\s*)?\d+",
        r"\b\d+\s*in\s*stock\b",
        r"\bonly\s*\d+\s*left\b",
        r"\d+\s*units?\s*(?:available|in\s*stock)\b",
    )

    def test_no_specific_stock_number_anywhere(self):
        text = _FewShotFile.text()
        for pat in self.STOCK_NUMBERS:
            # Inline the IGNORECASE flag (older unittest doesn't accept flags=)
            self.assertNotRegex(
                text, "(?i)" + pat,
                msg=f"few_shot_examples quotes a specific stock count: {pat}")


class TestResolverFewShotStockSaysFirstCome(unittest.TestCase):
    """Case 4 (stock) must include the 'first-come, first-served' phrase
    or close variant."""

    def test_case_4_mentions_first_come_first_served(self):
        text = _FewShotFile.text()
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)
        case4 = blocks[4]
        self.assertTrue(
            "first-come, first-served" in case4.lower()
            or "first come, first served" in case4.lower(),
            "Case 4 must mention 'first-come, first-served' for stock inquiries",
        )


class TestResolverFewShotNotSaley(unittest.TestCase):
    """No upsell language anywhere in the few-shot."""

    UPSELL_PHRASES = (
        "we also offer",
        "you might also like",
        "consider our",
        "we recommend",
        "features include",
        "we suggest",
    )

    def test_no_upsell_phrases(self):
        text = _FewShotFile.text().lower()
        for phrase in self.UPSELL_PHRASES:
            self.assertNotIn(phrase, text,
                f"few_shot_examples contains upsell phrase: {phrase!r}")


class TestResolverFewShotPolicyCitationFormat(unittest.TestCase):
    """Citations use `<policy_id> §<clause>` — no hyperlinks, no prose."""

    def test_citations_use_section_symbol(self):
        text = _FewShotFile.text()
        # Find citations in the form "<policy_id> §<clause>"
        citations = re.findall(
            r"([a-z][a-z_-]+)-?policy\s*§\s*(\d+(?:\.\d+)?)",
            text, re.IGNORECASE,
        )
        self.assertGreaterEqual(len(citations), 3,
            "expected at least 3 <policy_id> §<clause> citations across examples")

    def test_no_hyperlinks(self):
        text = _FewShotFile.text()
        self.assertNotIn("](http", text,
            "few_shot_examples must not contain markdown hyperlinks")
        self.assertNotIn("https://", text,
            "few_shot_examples must not contain URLs")

    def test_no_prose_section_references(self):
        text = _FewShotFile.text()
        # Should not have "section X.Y" or "clause X.Y" in prose form
        self.assertNotRegex(text, r"\bsection\s+\d+(?:\.\d+)?",
            "few_shot_examples should use § symbol, not 'section N' prose")


class TestResolverFewShotHiltReasonForHiltActions(unittest.TestCase):
    """Cases 1 (hilt_other) and 2 (hilt_refund) have a populated
    hilt_reason; cases 3 and 4 have hilt_reason: null."""

    def _block(self, text: str, example_num: int) -> str:
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)
        return blocks[example_num]

    HILT_REASON_FIELDS = (
        "policy_clause", "reason_short", "reason_long",
        "recommended_action", "action_target", "urgency",
        "human_skill_required",
    )

    def test_hilt_cases_have_full_hilt_reason(self):
        text = _FewShotFile.text()
        for n in (1, 2):
            b = self._block(text, n)
            # Must contain all 7 field names in a JSON-like block
            for f in self.HILT_REASON_FIELDS:
                self.assertIn(f, b,
                    f"Case {n} hilt_reason missing field: {f}")

    def test_non_hilt_cases_have_null_hilt_reason(self):
        text = _FewShotFile.text()
        for n in (3, 4):
            b = self._block(text, n)
            self.assertRegex(b, r'"hilt_reason"\s*:\s*null',
                f"Case {n} (non-hilt) must have hilt_reason: null")


class TestResolverFewShotHiltOtherVsHiltRefund(unittest.TestCase):
    """Case 1 = hilt_other (replacement, no money movement).
    Case 2 = hilt_refund (money movement)."""

    def _block(self, text: str, example_num: int) -> str:
        blocks = re.split(r"^## (?:Example|Case)\s+\d+", text, flags=re.MULTILINE)
        return blocks[example_num]

    def test_case_1_is_hilt_other(self):
        text = _FewShotFile.text()
        b = self._block(text, 1)
        self.assertRegex(b, r'"action"\s*:\s*"hilt_other"',
            "Case 1 (defective, replacement) must be hilt_other")

    def test_case_2_is_hilt_refund(self):
        text = _FewShotFile.text()
        b = self._block(text, 2)
        self.assertRegex(b, r'"action"\s*:\s*"hilt_refund"',
            "Case 2 (duplicate charge) must be hilt_refund")


class TestResolverUserMessageNoToolCalls(unittest.TestCase):
    """The user message passed to the resolver must not include the
    retriever's tool_calls audit log. Only the resolved results
    (customer, order, payments, products, policies) are useful for
    drafting."""

    def test_render_user_drops_tool_calls(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        ctx = {
            "customer": {"found": True, "row": {"full_name": "X"}},
            "order": {"found": True, "row": {"order_id": "BM1"}},
            "payments": [],
            "products": [],
            "policies": [],
            "tool_calls": [
                {"tool": "lookup_customer", "args": {}, "result_keys": [],
                 "duration_ms": 5},
            ],
        }
        agent = ResolverAgent()
        msg = agent._render_user("S", "B", "a@b.com", ctx)
        # The user message contains a JSON dump of the context.
        # The tool_calls key must NOT appear in that dump.
        self.assertNotIn('"tool_calls"', msg,
            "_render_user must not include the retriever's tool_calls audit log")
        # But the resolved results must still be present
        self.assertIn('"customer"', msg)
        self.assertIn('"order"', msg)
        self.assertIn('"policies"', msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# Phase 5E: state_examples (5th modular prompt file)
# ---------------------------------------------------------------------------
class TestResolverHasStateExamplesFile(unittest.TestCase):
    """The resolver must own a `state_examples` file: a terse per-state
    routing reference, sibling to few_shot_examples."""

    @staticmethod
    def path() -> Path:
        return (Path(__file__).resolve().parent.parent
                / "src" / "agent" / "resolver_agent" / "prompts"
                / "state_examples")

    def test_file_exists_and_nonempty(self):
        p = self.path()
        self.assertTrue(p.exists(), f"missing: {p}")
        self.assertGreater(p.stat().st_size, 100,
            f"{p} is suspiciously small ({p.stat().st_size} bytes)")

    def test_mentions_all_four_states(self):
        text = self.path().read_text()
        for state in ("auto_send", "hilt_refund", "hilt_other", "escalate"):
            self.assertIn(state, text,
                f"state_examples missing state: {state!r}")


class TestResolverStateExamplesTerse(unittest.TestCase):
    def test_under_100_lines(self):
        """The user asked for 'short'. Keep this file as a focused
        reference, not a wall of text."""
        p = TestResolverHasStateExamplesFile.path()
        n_lines = sum(1 for _ in p.read_text().splitlines())
        self.assertLessEqual(n_lines, 100,
            f"state_examples is {n_lines} lines; should be ≤100")


class TestResolverStateExamplesNoPII(unittest.TestCase):
    PII_EMAIL_RE = re.compile(
        r"\b[A-Za-z0-9_.+-]+@(?:gmail|yahoo|outlook|hotmail|test|proton)\."
        r"(?:com|org|net|io)\b",
        re.IGNORECASE,
    )
    PII_NAME_WORDS = ("Sanya", "Anjali", "Manish", "Neha", "Rishit",
                      "Priya", "Vikram", "Divya", "Karan", "Rohit",
                      "Sanya Malhotra")

    def test_no_real_email_addresses(self):
        text = TestResolverHasStateExamplesFile.path().read_text()
        self.assertEqual(self.PII_EMAIL_RE.findall(text), [],
            f"state_examples contains real email addresses")

    def test_no_real_customer_names(self):
        text = TestResolverHasStateExamplesFile.path().read_text()
        for name in self.PII_NAME_WORDS:
            self.assertNotRegex(text, rf"\b{re.escape(name)}\b",
                f"state_examples contains real customer name: {name!r}")


class TestResolverStateExamplesHasBoundary(unittest.TestCase):
    """The hilt_refund vs hilt_other edge is the most ambiguous routing
    decision. The state file must explain how to choose between them."""

    def test_explains_hilt_refund_vs_hilt_other(self):
        text = TestResolverHasStateExamplesFile.path().read_text().lower()
        self.assertIn("hilt_refund", text)
        self.assertIn("hilt_other", text)
        # Should mention the tiebreaker (refund vs replacement).
        self.assertTrue(
            "refund" in text and "replacement" in text,
            "state_examples must explain refund vs replacement tiebreaker")


class TestResolverFewShotReferencesStateFile(unittest.TestCase):
    """The headline few-shot should cross-reference the state file so
    the model knows the focused deep-dive exists."""

    def test_few_shot_has_cross_reference(self):
        few = (Path(__file__).resolve().parent.parent
               / "src" / "agent" / "resolver_agent" / "prompts"
               / "few_shot_examples").read_text()
        # Should mention state_examples or "Routing decisions" or "deep dive"
        self.assertTrue(
            "state_examples" in few
            or "Routing decisions" in few
            or "deep dive" in few.lower(),
            "few_shot_examples should cross-reference the state file")


class TestResolverSystemPromptHasStateSection(unittest.TestCase):
    """The resolver's assembled system_prompt must contain the new
    '## Routing decisions (deep dive)' section header."""

    def test_resolver_system_prompt_has_routing_section(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        sp = ResolverAgent().system_prompt
        self.assertIn("## Routing decisions", sp,
            "resolver system_prompt missing the new state_examples section")

    def test_routing_section_after_examples_before_guardrails(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        sp = ResolverAgent().system_prompt
        idx_examples = sp.find("## Examples")
        idx_routing = sp.find("## Routing decisions")
        idx_guardrails = sp.find("## Guardrails")
        # All three must be present
        self.assertGreater(idx_examples, 0)
        self.assertGreater(idx_routing, 0)
        self.assertGreater(idx_guardrails, 0)
        # Order: Examples < Routing decisions < Guardrails
        self.assertLess(idx_examples, idx_routing)
        self.assertLess(idx_routing, idx_guardrails)


class TestRetrieverDoesNotLoadStateExamples(unittest.TestCase):
    """The state file is resolver-specific. The retriever's
    system_prompt must NOT contain the '## Routing decisions' section."""

    def test_retriever_has_no_routing_section(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        sp = RetrieverAgent().system_prompt
        self.assertNotIn("## Routing decisions", sp,
            "retriever should not include the resolver's state_examples section")
        # Retriever still has its 4 standard sections
        for header in ("## Your Role", "## How You Use Your Tools",
                      "## Examples", "## Guardrails"):
            self.assertIn(header, sp,
                f"retriever missing standard section: {header}")


class TestPromptBuilderSectionOrder(unittest.TestCase):
    """The PromptBuilder.SECTION_ORDER must place state_examples
    between few_shot_examples and guardrails."""

    def test_state_examples_in_section_order(self):
        from src.agent.prompt_builder import PromptBuilder
        order = list(PromptBuilder.SECTION_ORDER)
        self.assertIn("state_examples", order,
            "state_examples missing from SECTION_ORDER")
        idx_examples = order.index("few_shot_examples")
        idx_state = order.index("state_examples")
        idx_guardrails = order.index("guardrails")
        self.assertLess(idx_examples, idx_state,
            "few_shot_examples should come before state_examples")
        self.assertLess(idx_state, idx_guardrails,
            "state_examples should come before guardrails")


class TestAgentPromptsHasStateField(unittest.TestCase):
    """The shared AgentPrompts dataclass must accept state_examples."""

    def test_state_examples_field_present(self):
        from src.agent.prompts import AgentPrompts
        import dataclasses
        fields = {f.name for f in dataclasses.fields(AgentPrompts)}
        self.assertIn("state_examples", fields,
            "AgentPrompts missing state_examples field")

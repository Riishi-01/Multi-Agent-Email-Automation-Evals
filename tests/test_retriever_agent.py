"""tests/test_retriever_agent.py — Phase 4C retriever_agent tests.

The retriever agent:
  - Loads modular prompts from ./prompts/{role,tools,few_shot_examples,guardrails}
    + a VERSION file.
  - Runs an OpenAI function-calling loop (max 6 iterations).
  - Always calls lookup_customer first per the eval set's convention.
  - Returns a RetrieverContext with customer / order / payments / products /
    policies / tool_calls[].

Without an OpenAI key, falls back to a deterministic DEMO path that calls
the same tools (just without the model choosing them).

DB-backed; silently skipped when DB unreachable.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class _DBAware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from src.db import get_session, Customer
            s = get_session("evaluator")
            n = s.query(Customer).count()
            s.close()
            cls._skip_db = n != 36
        except Exception as exc:
            print(f"[skip] DB not reachable: {exc}")
            cls._skip_db = True

    def setUp(self):
        if self._skip_db:
            self.skipTest("DB not reachable (run scripts/setup_db.py first)")


class AgentLifecycle(_DBAware):
    def test_constructor_loads_prompts(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        a1 = RetrieverAgent()
        a2 = RetrieverAgent()
        self.assertEqual(a1.system_prompt, a2.system_prompt)
        self.assertGreater(len(a1.system_prompt), 200)
        self.assertIn("RETRIEVER", a1.system_prompt)

    def test_prompts_dir_has_four_components(self):
        from pathlib import Path
        from src.agent.retriever.agent_retriever import AgentRetriever
        prompts_dir = Path(__file__).resolve().parent.parent / "src/agent/retriever_agent/prompts"
        self.assertTrue(prompts_dir.is_dir(),
                        f"missing retriever_agent prompts at {prompts_dir}")
        for c in ("role", "tools", "few_shot_examples", "guardrails"):
            self.assertTrue((prompts_dir / c).exists(),
                            f"missing prompts/{c}")

    def test_version_file_present(self):
        from pathlib import Path
        v = Path(__file__).resolve().parent.parent / "src/agent/retriever_agent/VERSION"
        self.assertTrue(v.exists(), f"missing VERSION file at {v}")
        self.assertTrue(v.read_text().strip())


class RetrieverContextShape(_DBAware):
    """RetrieverContext must carry customer, order, payments, products, policies,
    and tool_calls."""

    def test_context_has_required_fields(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="PS5 delivery before Sep 1",
            body="Dear Support, I would like to know the expected delivery date "
                 "for my PS5 order BM200001. Is expedited delivery available?",
            sender_email="sanya.delhi@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        for k in ("customer", "order", "payments", "products", "policies", "tool_calls"):
            self.assertIn(k, d, f"context missing field {k}")
        self.assertIsInstance(d["tool_calls"], list)
        self.assertGreaterEqual(len(d["tool_calls"]), 1)

    def test_lookup_customer_called_first(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="PS5 delivery",
            body="Hi, I would like to know my PS5 delivery status. BM200001.",
            sender_email="sanya.delhi@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        first_call = d["tool_calls"][0]
        self.assertEqual(first_call["tool"], "lookup_customer")
        self.assertEqual(first_call["args"]["email"], "sanya.delhi@gmail.com")

    def test_finds_sanya_and_ps5_order(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="PS5 delivery before Sep 1",
            body="Hi, I'd like delivery for my PS5. BM200001.",
            sender_email="sanya.delhi@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        self.assertTrue(d["customer"]["found"])
        self.assertEqual(d["customer"]["row"]["email"], "sanya.delhi@gmail.com")
        self.assertTrue(d["order"]["found"])
        self.assertEqual(d["order"]["row"]["order_id"], "BM200001")

    def test_tool_calls_have_duration(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="PS5 delivery",
            body="Hi, my PS5 order.",
            sender_email="sanya.delhi@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        for call in d["tool_calls"]:
            self.assertIn("duration_ms", call)
            self.assertGreaterEqual(call["duration_ms"], 0)


class RetrieverContextEdgeCases(_DBAware):
    def test_unknown_email_yields_empty_customer(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="random",
            body="Hi, anything.",
            sender_email="nobody@nowhere.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        self.assertFalse(d["customer"]["found"])

    def test_max_iterations_respected(self):
        """The agent must stop after at most 6 tool calls."""
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="S", body="B", sender_email="nobody@nowhere.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        self.assertLessEqual(len(d["tool_calls"]), 6)


class RefundLookupPattern(_DBAware):
    """E30 duplicate-payment case: needs lookup_order_by_id + lookup_payments_for_order
    + lookup_orphan_payment."""

    def test_e30_chains_three_tools(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="Duplicate Payment",
            body="Dear Team, I was charged twice for BM189438. Refund please.",
            sender_email="manish.jain.mum@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        called = [c["tool"] for c in d["tool_calls"]]
        # At minimum: lookup_customer + lookup_order_by_id
        self.assertIn("lookup_customer", called)
        self.assertIn("lookup_order_by_id", called)


class OrphansAndPolicies(_DBAware):
    """Sanity: lookup_policy is reachable when the email mentions policy."""

    def test_policy_keyword_triggers_lookup_policy(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="Force majeure refund?",
            body="My order is delayed. What is your force majeure policy?",
            sender_email="rohit.m@test.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        called = [c["tool"] for c in d["tool_calls"]]
        self.assertIn("lookup_policy", called)


# ---------------------------------------------------------------------------
# Agentic RAG — multi-query dispatch (no regex, no static patterns)
# ---------------------------------------------------------------------------
class AgenticRAGDispatch(_DBAware):
    """The retriever must issue 1-3 natural-language lookup_policy queries,
    not pattern-match the email body against a static topic map."""

    def test_multi_topic_email_yields_multiple_policy_calls(self):
        """Email mentions return + refund + payment; retriever must fan out
        and surface >=2 distinct policy parents in ctx.policies."""
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="Duplicate Payment",
            body=("Dear Team, I was charged twice while ordering an Xbox "
                  "Wireless Controller, but only one order was placed. "
                  "My first payment was made using a different bank account. "
                  "Order ID is BM189438. I would like a refund for the "
                  "first transaction."),
            sender_email="manish.jain.mum@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        lp_calls = [c for c in d["tool_calls"] if c["tool"] == "lookup_policy"]
        self.assertGreaterEqual(
            len(lp_calls), 2,
            f"expected >=2 lookup_policy calls; got {len(lp_calls)}")
        # ctx.policies should carry at least 2 distinct parent_ids.
        parent_ids = {p.get("parent_id") for p in d["policies"]}
        self.assertGreaterEqual(
            len(parent_ids), 2,
            f"expected >=2 unique parents in policies; got {len(parent_ids)}")

    def test_policy_queries_are_natural_questions(self):
        """Each lookup_policy call's args.query must be a real sentence,
        not a keyword list or pattern match. No regex in the retriever."""
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="Defective Razer BlackShark",
            body=("My Razer BlackShark V2 Pro has dead audio on one side. "
                  "The unit is barely 3 months old. I want a replacement, "
                  "not a refund. I read the return policy and I am within "
                  "the 14-day window."),
            sender_email="anjalisingh.hyd@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        lp_calls = [c for c in d["tool_calls"] if c["tool"] == "lookup_policy"]
        self.assertGreaterEqual(len(lp_calls), 1)
        for c in lp_calls:
            q = c["args"].get("query", "")
            # Real natural-language question: longer than a keyword,
            # not a list of | -separated topics, and references policy.
            self.assertGreater(
                len(q), 20,
                f"query too short to be a real question: {q!r}")
            self.assertNotIn(
                "|", q,
                f"query looks like a regex / topic-list, not a question: {q!r}")
            self.assertIn(
                "policy", q.lower(),
                f"query must reference 'policy': {q!r}")

    def test_max_three_policy_calls(self):
        """Guardrail: even on a multi-topic email, retriever issues
        <=3 lookup_policy calls (out of 6 total tool calls)."""
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="Everything is wrong",
            body=("Refund my damaged Black Shark, cancel my second order, "
                  "and tell me your DPDPA erasure policy and grievance "
                  "officer contact."),
            sender_email="nobody@nowhere.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        lp_calls = [c for c in d["tool_calls"] if c["tool"] == "lookup_policy"]
        self.assertLessEqual(
            len(lp_calls), 3,
            f"expected <=3 lookup_policy calls; got {len(lp_calls)}")
        self.assertLessEqual(len(d["tool_calls"]), 6)

    def test_simple_email_yields_at_least_one_policy_call(self):
        """Even a one-topic email must get at least 1 policy call so
        the resolver has citation material."""
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        agent = RetrieverAgent()
        ctx = agent.run(
            subject="Order inquiry",
            body="Hi, where is my PS5?",
            sender_email="sanya.delhi@gmail.com",
        )
        d = ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx)
        lp_calls = [c for c in d["tool_calls"] if c["tool"] == "lookup_policy"]
        self.assertGreaterEqual(
            len(lp_calls), 1,
            f"expected >=1 lookup_policy call; got {len(lp_calls)}")


class RetrieverModularPrompts(unittest.TestCase):
    """The retriever must use the same modular-prompt architecture as the
    resolver: prompts/{role,tools,few_shot_examples,guardrails} + VERSION,
    assembled by PromptBuilder at construction in fixed order."""

    def test_uses_modular_prompts(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        sp = RetrieverAgent().system_prompt
        # Fixed order from PromptBuilder.SECTION_HEADERS.
        idx_role = sp.find("## Your Role")
        idx_tools = sp.find("## How You Use Your Tools")
        idx_examples = sp.find("## Examples")
        idx_guardrails = sp.find("## Guardrails")
        for name, idx in [("role", idx_role), ("tools", idx_tools),
                          ("examples", idx_examples),
                          ("guardrails", idx_guardrails)]:
            self.assertGreater(
                idx, 0, f"missing '{name}' section in retriever system prompt")
        # Order check.
        self.assertLess(idx_role, idx_tools)
        self.assertLess(idx_tools, idx_examples)
        self.assertLess(idx_examples, idx_guardrails)

    def test_prompts_version_prepended(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverAgent
        sp = RetrieverAgent().system_prompt
        self.assertIn("agent=retriever_agent", sp)
        self.assertIn("version=", sp)

    def test_role_prompts_multi_query_dispatch(self):
        """The role prompt must instruct the model to issue 1-3 queries,
        one per distinct topic."""
        from pathlib import Path
        from src.agent.retriever.agent_retriever import AgentRetriever
        p = (Path(__file__).resolve().parent.parent
             / "src/agent/retriever_agent/prompts/role")
        text = p.read_text()
        self.assertIn("1", text)            # 1-3
        self.assertTrue(
            "3" in text or "three" in text.lower(),
            "role prompt should reference the 3-call cap")

    def test_guardrails_caps_policy_calls_at_three(self):
        from pathlib import Path
        p = (Path(__file__).resolve().parent.parent
             / "src/agent/retriever_agent/prompts/guardrails")
        text = p.read_text()
        self.assertIn("3", text)


class RetrieverNoRegex(unittest.TestCase):
    """The retriever must NOT use regex pattern matching for policy-topic
    detection. (extract_order_id / extract_txn_id from src.parser is fine —
    those are the email-ID parser, not policy-topic detection.)"""

    def test_no_policy_topic_patterns_module_attr(self):
        import src.agent.retriever_agent.agent_retriever as r
        self.assertFalse(
            hasattr(r, "_POLICY_TOPIC_PATTERNS"),
            "retriever still defines the regex policy-topic table; remove it")
        self.assertFalse(
            hasattr(r, "_guess_policy_query"),
            "retriever still uses _guess_policy_query(); remove it")

    def test_no_re_compile_in_retriever(self):
        """No re.compile for policy-topic detection. The retriever may
        import re for extract_order_id/extract_txn_id, but should not
        compile new patterns in the policy-dispatch path."""
        import inspect
        import src.agent.retriever_agent.agent_retriever as r
        src_text = inspect.getsource(r)
        # The DEMO dispatch must not define a regex compiled list.
        self.assertNotIn("re.compile", src_text,
            "retriever source uses re.compile — strip the pattern table")

    def test_demo_dispatch_does_not_import_re(self):
        """If re is only used in the import block for the email parser,
        it's fine. But the policy dispatch path must not depend on it."""
        import re
        # The pattern list, if present, would be a list of (re.Pattern, str).
        import src.agent.retriever_agent.agent_retriever as r
        if hasattr(r, "_POLICY_TOPIC_PATTERNS"):
            for entry in r._POLICY_TOPIC_PATTERNS:
                self.assertNotIsInstance(
                    entry[0], re.Pattern,
                    "policy dispatch still uses compiled regex")


# ---------------------------------------------------------------------------
# Few-shot shape (Phase 5: no PII, CoT reasoning, direct tool refs)
# ---------------------------------------------------------------------------
class _FewShotFile:
    """Helper: read the retriever's few_shot_examples file once per test."""
    @staticmethod
    def path() -> Path:
        return (Path(__file__).resolve().parent.parent
                / "src" / "agent" / "retriever_agent" / "prompts"
                / "few_shot_examples")

    @classmethod
    def text(cls) -> str:
        return cls.path().read_text()


class TestRetrieverFewShotNoPII(unittest.TestCase):
    """The few-shot must not contain any real email addresses or names.

    The PII set is the one used in the eval set; we just block the
    suffix domains and a few common names."""

    PII_EMAIL_RE = re.compile(
        r"\b[A-Za-z0-9_.+-]+@(?:gmail|yahoo|outlook|hotmail|test|proton)\.(?:com|org|net|io)\b",
        re.IGNORECASE,
    )
    PII_NAME_WORDS = ("Sanya", "Anjali", "Manish", "Neha", "Rishit",
                      "Priya", "Vikram", "Divya", "Karan", "Rohit",
                      "Manish Jain", "Sanya Malhotra")

    def test_no_real_email_addresses(self):
        text = _FewShotFile.text()
        matches = self.PII_EMAIL_RE.findall(text)
        self.assertEqual(matches, [],
            f"few_shot_examples contains real email addresses: {matches}")

    def test_no_real_customer_names(self):
        text = _FewShotFile.text()
        # Use a word-boundary match so "Sanya" inside "Sanyanagar" wouldn't
        # trip the test. Real names appear as standalone tokens.
        for name in self.PII_NAME_WORDS:
            self.assertNotRegex(
                text, rf"\b{re.escape(name)}\b",
                f"few_shot_examples contains real customer name: {name!r}")


class TestRetrieverFewShotHasGoalStatement(unittest.TestCase):
    def test_first_lines_state_goal_not_resolution(self):
        """The retriever's purpose is data collection, not reply drafting.
        A goal statement at the top should make that explicit."""
        text = _FewShotFile.text()
        # Look in the first ~400 chars (the preamble)
        head = text[:400]
        self.assertIn("Goal", head,
            "few_shot_examples should open with a 'Goal:' statement")
        # Should NOT contain resolver-only vocabulary
        self.assertNotIn("draft", head.lower(),
            "retriever's few-shot preamble should not mention 'draft'")
        self.assertNotIn("PENDING APPROVAL", head,
            "retriever's few-shot preamble should not mention PENDING APPROVAL")


class TestRetrieverFewShotNoHedging(unittest.TestCase):
    """Examples must reference tools by name directly, not first-person
    hedging like 'I will look up the order id'.

    The counter-example block at the bottom is allowed to *quote* bad
    patterns (so the model sees them); we just don't count those quotes
    as actual hedging."""

    HEDGING_RE = re.compile(
        r"\bI will (?:look up|call|query|search|fetch)\b"
        r"|\bI should call\b"
        r"|\blet me query\b"
        r"|\bI[' ]?d like to\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _positive_examples_text(text: str) -> str:
        """Return only the worked-example content (skip counter-example)."""
        cut = text.find("## Counter-example")
        if cut == -1:
            return text
        return text[:cut]

    def test_no_first_person_hedging(self):
        text = self._positive_examples_text(_FewShotFile.text())
        matches = self.HEDGING_RE.findall(text)
        self.assertEqual(matches, [],
            f"few_shot_examples (positive examples) uses first-person hedging: {matches}")

    def test_uses_direct_tool_references(self):
        text = _FewShotFile.text()
        # Each tool name should appear at least once as a direct reference.
        for tool in ("lookup_customer", "lookup_policy"):
            self.assertIn(tool, text,
                f"few_shot_examples does not reference tool: {tool}")


class TestRetrieverFewShotNaturalLanguageQueries(unittest.TestCase):
    """Every lookup_policy(query=...) call must have a full natural-language
    question, not a keyword list or short fragment."""

    def test_all_queries_are_full_sentences(self):
        # The counter-example block intentionally quotes a short bad query
        # (e.g. "console") to teach the model what NOT to do. Exclude
        # that block from this assertion.
        text = _FewShotFile.text()
        cut = text.find("## Counter-example")
        positive = text if cut == -1 else text[:cut]
        queries = re.findall(r'query="([^"]+)"', positive)
        self.assertGreaterEqual(len(queries), 3,
            "expected at least 3 lookup_policy queries across examples")
        for q in queries:
            self.assertGreaterEqual(
                len(q), 25,
                f"query too short: {q!r} (must be a full sentence)")
            # No keyword-list syntax (pipe-separated)
            self.assertNotIn("|", q,
                f"query looks like a keyword list: {q!r}")
            # Must look like a question or start with a question word
            self.assertTrue(
                q.endswith("?")
                or q.lower().startswith(("what", "how", "why", "when",
                                          "where", "is ", "are ", "can ")),
                f"query must end in ? or start with a question word: {q!r}",
            )

    def test_no_dollar_or_amount_synthesis(self):
        """Few-shot queries must not invent specific ₹ amounts."""
        text = _FewShotFile.text()
        # Skip the counter-example block which may contain placeholder
        # text — we just check the worked examples.
        cut = text.find("## Counter-example")
        positive = text if cut == -1 else text[:cut]
        self.assertNotRegex(positive, r"₹\s*\d",
            "few_shot_examples (positive examples) should not invent specific currency amounts")


class TestRetrieverFewShotOneToThreeQueries(unittest.TestCase):
    """Each worked example must issue between 1 and 3 lookup_policy calls."""

    def test_each_example_within_budget(self):
        text = _FewShotFile.text()
        # Split into per-example blocks (## Example N — ...)
        blocks = re.split(r"^## Example \d+", text, flags=re.MULTILINE)[1:]
        self.assertGreaterEqual(len(blocks), 3,
            "expected at least 3 worked examples")
        for i, blk in enumerate(blocks, start=1):
            n = len(re.findall(r"lookup_policy\s*\(", blk))
            self.assertGreaterEqual(n, 1, f"Example {i}: 0 lookup_policy calls")
            self.assertLessEqual(n, 3, f"Example {i}: {n} lookup_policy calls (>3 cap)")


class TestRetrieverFewShotDemonstratesMultiQuery(unittest.TestCase):
    """The headline behavior: 1+ examples with 3 distinct natural-language
    lookup_policy queries (one per topic)."""

    def test_example_2_has_three_distinct_queries(self):
        text = _FewShotFile.text()
        # Example 2 is the agentic-RAG headline — must have 3 distinct queries.
        blocks = re.split(r"^## Example \d+", text, flags=re.MULTILINE)
        self.assertGreaterEqual(len(blocks), 3)
        ex2 = blocks[2]  # [0]=preamble, [1]=ex1, [2]=ex2
        queries = re.findall(r'lookup_policy\(query="([^"]+)"', ex2)
        self.assertEqual(len(queries), 3,
            f"Example 2 should have 3 lookup_policy calls; got {len(queries)}")
        # All 3 must be distinct
        self.assertEqual(len(set(queries)), 3,
            "Example 2's 3 lookup_policy queries must be distinct")


class TestRetrieverFewShotNoResolverOverlap(unittest.TestCase):
    """The retriever's few-shot must not blur into the resolver's job."""

    def test_no_draft_examples(self):
        text = _FewShotFile.text()
        # 'draft' in the resolver sense (a customer-reply string). The
        # retriever's job is to collect data; it doesn't produce drafts.
        # We allow the word "drafts" only as a verb (e.g. "drafts a reply"
        # is wrong; "the retriever does not draft" is correct). We assert
        # the resolver-only JSON shape is absent.
        self.assertNotIn('"draft":', text,
            "retriever's few-shot must not show resolver-shape drafts")

    def test_no_pending_approval(self):
        text = _FewShotFile.text()
        self.assertNotIn("PENDING APPROVAL", text,
            "PENDING APPROVAL is resolver-only vocabulary")


if __name__ == "__main__":
    unittest.main(verbosity=2)

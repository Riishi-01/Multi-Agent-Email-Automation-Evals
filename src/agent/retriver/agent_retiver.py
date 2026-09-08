"""DEPRECATED: spelling-correction shim.

The original typo'd path was ``src/agent/retriver/agent_retiver.py``
with class ``AgentRetiver``. The canonical module is now at
``src/agent/retriever/agent_retriever.py`` with class
``AgentRetriever``.

This shim re-exports the new class under the old name so existing
imports (``from src.agent.retriver.agent_retiver import AgentRetiver``)
keep working. New code should import from the canonical path.

This shim will be removed in a future release.
"""
from __future__ import annotations

import warnings as _warnings

from src.agent.retriever.agent_retriever import AgentRetriever as AgentRetiver


_warnings.warn(
    "src.agent.retriver.agent_retiver is deprecated; "
    "import from src.agent.retriever.agent_retriever instead.",
    DeprecationWarning,
    stacklevel=2,
)


__all__ = ["AgentRetiver"]

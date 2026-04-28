"""LangGraph workflow definition for the agentic job search pipeline.

Graph topology (Phase 2):
  preflight → discovery → enrichment → scoring → reflection → review → output

LangSmith tracing is controlled by LANGSMITH_TRACING in .env. At import time this
module maps LANGSMITH_TRACING=true → LANGCHAIN_TRACING_V2=true (the env var that the
LangSmith SDK reads). When LANGSMITH_TRACING=false, LANGCHAIN_TRACING_V2 is explicitly
set to "false" so any ambient env-var override is neutralised.
"""
from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph

from role_scout.config import Settings
from role_scout.models.state import JobSearchState
from role_scout.nodes.discovery import discovery_node
from role_scout.nodes.enrichment import enrichment_node
from role_scout.nodes.output import output_node
from role_scout.nodes.preflight import preflight_node
from role_scout.nodes.reflection import reflection_node
from role_scout.nodes.review import review_node
from role_scout.nodes.scoring import scoring_node


def _configure_langsmith(settings: Settings) -> None:
    """Map LANGSMITH_TRACING → LANGCHAIN_TRACING_V2 so the LangSmith SDK can read it.

    Uses setdefault so an operator who already exported LANGCHAIN_TRACING_V2 manually
    won't have it overridden when tracing is enabled. When disabled, we explicitly set
    the var to "false" to neutralise any ambient override.
    """
    if settings.LANGSMITH_TRACING:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"


def _post_discovery_router(state: JobSearchState) -> str:
    """Short-circuit to output when discovery is crippled (2+ sources failed)."""
    return "output" if state.get("cancel_reason") else "enrichment"


def build_graph(
    checkpointer: MemorySaver | None = None,
    settings: Settings | None = None,
) -> StateGraph:
    """Construct and compile the job search StateGraph.

    LangSmith tracing: set LANGSMITH_TRACING=true in .env to enable tracing.
    This module maps it to LANGCHAIN_TRACING_V2 inside build_graph(), which is
    the environment variable the LangSmith SDK reads. When LANGSMITH_TRACING=false
    (the default), LANGCHAIN_TRACING_V2 is explicitly disabled so no network
    calls are made to LangSmith.

    Args:
        checkpointer: Optional MemorySaver; defaults to a fresh in-memory one.
        settings: Optional pre-constructed Settings; loaded from env when None.
    """
    try:
        _configure_langsmith(settings or Settings())
    except Exception:
        # In test environments missing required env vars, skip tracing config
        # rather than crashing graph construction.
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    builder: StateGraph = StateGraph(JobSearchState)

    builder.add_node("preflight", preflight_node)
    builder.add_node("discovery", discovery_node)
    builder.add_node("enrichment", enrichment_node)
    builder.add_node("scoring", scoring_node)
    builder.add_node("reflection", reflection_node)
    builder.add_node("review", review_node)
    builder.add_node("output", output_node)

    builder.set_entry_point("preflight")
    builder.add_edge("preflight", "discovery")
    builder.add_conditional_edges("discovery", _post_discovery_router, {"enrichment": "enrichment", "output": "output"})
    builder.add_edge("enrichment", "scoring")
    builder.add_edge("scoring", "reflection")
    builder.add_edge("reflection", "review")
    builder.add_edge("review", "output")
    builder.add_edge("output", END)

    if checkpointer is None:
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("role_scout.compat.models", "CandidateProfile"),
                ("role_scout.compat.models", "ScoredJob"),
                ("role_scout.models.core", "SourceHealthEntry"),
            ]
        )
        checkpointer = MemorySaver(serde=serde)
    return builder.compile(checkpointer=checkpointer)

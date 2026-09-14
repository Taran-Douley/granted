"""Fast offline smoke tests — no AWS, no network.

    pip install "granted[dev]"
    pytest

These assert the package imports, the agent graph assembles, the silence gate
blocks by default, and the AgentCore entrypoint's health path works without any
credentials. The full offline suite is ``scripts/preflight_offline.py`` (30 checks).
"""

import types

from granted.agentcore_app import AGENTS, invoke


def test_package_imports():
    import granted  # noqa: F401
    from granted import criteria, dashboard, graph, run, triage  # noqa: F401


def test_agentcore_health_path_needs_no_aws():
    h = invoke({"ping": True})
    assert h["status"] == "ok"
    assert h["agents"] == AGENTS == ["matcher", "director", "timeliner", "drafter", "auditor"]
    assert h["model_provider"] == "amazon-bedrock"


def test_agentcore_requires_a_workspace():
    out = invoke({})
    assert "error" in out


def test_graph_assembles_offline():
    from granted.graph import build_graph
    assert build_graph() is not None


def test_silence_gate_blocks_without_a_match():
    from granted.graph import gate_passes
    assert gate_passes(types.SimpleNamespace(results={})) is False

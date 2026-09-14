"""Amazon Bedrock AgentCore Runtime entrypoint for Granted.

This exposes the whole Granted pipeline — the five-agent Strands graph and its
Bedrock models, unchanged — as an AgentCore Runtime app, so it can be deployed as
a managed, serverless HTTP endpoint instead of only run from a shell. The graph in
`graph.py` is the product; this module is just the deployment surface in front of
it.

The endpoint takes a workspace path and runs Granted's home-mode pipeline on it,
returning the run record the dashboard reads. A `{"ping": true}` payload is a
no-AWS health check, which is also how the local test exercises it.

Local (no AWS, no SDK needed for the health path):
    python -c "from granted.agentcore_app import invoke; print(invoke({'ping': True}))"

Local AgentCore dev server (needs `pip install granted[agentcore]`):
    python -m granted.agentcore_app          # serves on :8080

Deploy (needs Docker/Finch or CodeBuild + AWS credentials):
    pip install "granted[agentcore]"
    agentcore configure --entrypoint src/granted/agentcore_app.py
    agentcore launch
    agentcore invoke '{"home": "/path/to/workspace"}'
"""

from __future__ import annotations

import json
from pathlib import Path

# The AgentCore SDK is an optional extra. The module must still import without it
# so the entrypoint can be unit-tested and the health path works offline.
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
except Exception:  # noqa: BLE001 - any import/runtime failure means "SDK not present"
    app = None

AGENTS = ["matcher", "director", "timeliner", "drafter", "auditor"]


def _health() -> dict:
    return {
        "status": "ok",
        "service": "granted",
        "graph": "Strands Agents graph: matcher -> [gate] -> director | timeliner | drafter -> auditor",
        "agents": AGENTS,
        "model_provider": "amazon-bedrock",
    }


def invoke(payload, context=None):
    """AgentCore entrypoint.

    payload:
      {"ping": true}                 -> health check (no AWS)
      {"home": "<workspace path>"}   -> run Granted on that workspace, return the record
    """
    payload = payload or {}
    if payload.get("ping"):
        return _health()

    home = payload.get("home")
    if not home:
        return {"error": "send {'home': '<workspace path>'} to run Granted, "
                         "or {'ping': true} for a health check",
                **_health()}

    # Imported here so the health path (and the offline test) need no heavy deps.
    from .run import main as run_main

    rc = run_main(["--home", str(home), "--quiet"], prog="granted run (agentcore)")
    record_path = Path(home) / ".granted" / "run.json"
    record = (json.loads(record_path.read_text(encoding="utf-8"))
              if record_path.exists() else None)
    return {"ok": rc == 0, "workspace": str(home), "run": record}


# Register with the AgentCore runtime when the SDK is installed. At deploy time the
# runtime calls this on each request; offline, `invoke` is a plain function.
if app is not None:
    invoke = app.entrypoint(invoke)


def main():
    if app is None:
        raise SystemExit(
            "The AgentCore SDK is not installed.\n\n"
            "  pip install \"granted[agentcore]\"\n\n"
            "Then: python -m granted.agentcore_app  (local server on :8080)")
    app.run()


if __name__ == "__main__":
    main()

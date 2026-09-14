"""Count what a run actually cost, from the events rather than from guesswork.

The digest claims a number of model calls and a duration. Those have to come
from somewhere real, or the agent trace on the dashboard is decoration.

Strands emits hook events around every model call and every tool call, so this
counts them at the source: one recorder passed to every agent in the graph. It
also records which tools the matcher chose to call, which is the part worth
showing a person -- it is the difference between "the model decided" and "the
model checked these four things and then decided".
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from strands.hooks import (AfterModelCallEvent, AfterToolCallEvent,
                           BeforeInvocationEvent, HookProvider, HookRegistry)


@dataclass
class RunTrace:
    """What happened, counted as it happened."""

    model_calls: int = 0
    tool_calls: int = 0
    tools_used: Counter = field(default_factory=Counter)
    lookups: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    @property
    def duration_s(self) -> float:
        return round(time.monotonic() - self.started, 1)

    def as_dict(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "duration_s": self.duration_s,
            "tools_used": dict(self.tools_used),
            "lookups": self.lookups[:12],
        }


class Recorder(HookProvider):
    """Hook provider that writes into a RunTrace.

    Deliberately does nothing but count. A hook that mutates a run is a hook
    that has to be reasoned about when the run goes wrong.
    """

    def __init__(self, trace: RunTrace, tool_names: set[str] | None = None) -> None:
        self.trace = trace
        # Strands implements structured output as a tool call, so the schema
        # (Match, Draft, ...) shows up beside the real lookups. Counting it as a
        # lookup would inflate "what it checked" with the model talking to itself.
        self.tool_names = tool_names

    def register_hooks(self, registry: HookRegistry, **_: object) -> None:
        registry.add_callback(AfterModelCallEvent, self._model)
        registry.add_callback(AfterToolCallEvent, self._tool)

    def _model(self, event: AfterModelCallEvent) -> None:
        self.trace.model_calls += 1

    def _tool(self, event: AfterToolCallEvent) -> None:
        self.trace.tool_calls += 1
        name = _tool_name(event)
        if name and self.tool_names is not None and name not in self.tool_names:
            return
        if name:
            self.trace.tools_used[name] += 1
            args = _tool_input(event)
            self.trace.lookups.append(f"{name}({args})" if args else name)


def _tool_name(event) -> str | None:
    for path in ("tool_use", "tool_call", "current_tool_use"):
        obj = getattr(event, path, None)
        if isinstance(obj, dict) and obj.get("name"):
            return obj["name"]
        name = getattr(obj, "name", None)
        if name:
            return name
    return None


def _tool_input(event) -> str:
    for path in ("tool_use", "tool_call", "current_tool_use"):
        obj = getattr(event, path, None)
        data = obj.get("input") if isinstance(obj, dict) else getattr(obj, "input", None)
        if isinstance(data, dict) and data:
            return ", ".join(f"{v}" for v in list(data.values())[:2])
    return ""

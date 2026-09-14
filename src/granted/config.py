"""Model configuration.

Model IDs are Bedrock cross-region inference profile IDs, verified against
`aws bedrock list-inference-profiles` on 2026-09-08. The bare base model ID
(e.g. `anthropic.claude-haiku-4-5`) is NOT invocable and returns a 403 that
reads like a permissions problem but is not one. Always use the prefixed,
fully versioned profile ID.

Cost discipline: iterate on Haiku, record demo runs on Sonnet. Switching is a
single environment variable, so there is no reason to develop against the
expensive model.
"""

from __future__ import annotations

import os
import re

# Verified present in this account, us-west-2.
DEV_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEMO_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# `global.` variants route across regions and can survive throttling better.
# Worth knowing about if a recording session hits limits; do not switch
# pre-emptively, since a stable model makes runs comparable.
DEV_MODEL_GLOBAL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEMO_MODEL_GLOBAL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

DEFAULT_REGION = "us-west-2"


def model_id() -> str:
    """Resolve the model for this run.

    GRANTED_MODE=demo  -> Sonnet
    GRANTED_MODE=dev   -> Haiku (default)
    GRANTED_MODEL=...  -> explicit override, wins over both
    """
    override = os.environ.get("GRANTED_MODEL")
    if override:
        return override
    mode = os.environ.get("GRANTED_MODE", "dev").lower()
    return DEMO_MODEL if mode == "demo" else DEV_MODEL


def region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION


def provider() -> str:
    """bedrock (default) or anthropic."""
    return os.environ.get("GRANTED_PROVIDER", "bedrock").strip().lower()


def anthropic_model_id() -> str:
    """The same model, named the way the Anthropic API names it.

    Derived from the Bedrock id rather than kept in a second table, because two
    tables drift and the failure is a 404 three layers down. Bedrock dresses the
    id up with a routing prefix and a version suffix; strip both.

        us.anthropic.claude-haiku-4-5-20251001-v1:0  ->  claude-haiku-4-5-20251001
    """
    mid = model_id()
    for prefix in ("us.anthropic.", "eu.anthropic.", "apac.anthropic.",
                   "global.anthropic.", "anthropic."):
        if mid.startswith(prefix):
            mid = mid[len(prefix):]
            break
    return re.sub(r"-v\d+(?::\d+)?$", "", mid)


def resolve_model():
    """What `Agent(model=...)` should be given for the configured provider.

    Bedrock takes the inference profile id as a plain string. The Anthropic API
    needs a model object, and the SDK behind it is an optional extra, so the
    import stays inside the branch and the missing-dependency case says what to
    install instead of surfacing an ImportError from three frames down.
    """
    if provider() != "anthropic":
        return model_id()
    try:
        from strands.models.anthropic import AnthropicModel
    except ImportError as e:                                   # pragma: no cover
        raise RuntimeError(
            "GRANTED_PROVIDER=anthropic needs the Anthropic extra:\n"
            "    pip install 'strands-agents[anthropic]'"
        ) from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "GRANTED_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
    return AnthropicModel(model_id=anthropic_model_id(), max_tokens=4096)

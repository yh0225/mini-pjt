"""Chat model factory: Claude via the Anthropic API or Amazon Bedrock.

Provider selection (first match wins):
1. LLM_PROVIDER env var ("anthropic" or "bedrock")
2. ANTHROPIC_API_KEY set -> anthropic
3. AWS_ACCESS_KEY_ID set -> bedrock
"""

from __future__ import annotations

import os
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, model_validator

from travel_agent.config import AGENT_MODEL, LIGHT_MODEL

NULL_STRINGS = {"null", "none", "nil", "n/a", ""}


class LLMOutput(BaseModel):
    """Base for structured LLM outputs whose fields are often empty.

    Models sometimes return the *string* "null" (or "None", "") instead of an empty value,
    which fails validation for ints, dates, literals, and lists. Such top-level values are
    dropped before validation so the field's default applies.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_null_strings(cls, data):
        if isinstance(data, dict):
            return {
                k: v
                for k, v in data.items()
                if not (isinstance(v, str) and v.strip().lower() in NULL_STRINGS)
            }
        return data


Provider = Literal["anthropic", "bedrock"]
Role = Literal["agent", "light"]


def get_provider() -> Provider:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in ("anthropic", "bedrock"):
        return explicit  # type: ignore[return-value]
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("AWS_ACCESS_KEY_ID"):
        return "bedrock"
    raise RuntimeError(
        "No LLM credentials found. Set ANTHROPIC_API_KEY, or AWS_ACCESS_KEY_ID / "
        "AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION for Bedrock, in .env."
    )


def bedrock_model_id(model: str) -> str:
    """Map a first-party ID (claude-haiku-5-5) to a Bedrock inference profile ID.

    IDs that already look like Bedrock IDs (contain "anthropic.") are returned unchanged.
    """
    if "anthropic." in model:
        return model
    prefix = os.getenv("BEDROCK_PROFILE_PREFIX", "us")
    return f"{prefix}.anthropic.{model}"


def get_chat_model(role: Role = "agent", max_tokens: int = 16000) -> BaseChatModel:
    model = AGENT_MODEL if role == "agent" else LIGHT_MODEL
    provider = get_provider()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, max_tokens=max_tokens, max_retries=3)

    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=bedrock_model_id(model),
        region_name=os.getenv("AWS_DEFAULT_REGION"),
        max_tokens=max_tokens,
    )

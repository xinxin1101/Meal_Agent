import os
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class SiliconFlowSettings:
    api_key: str | None
    model: str | None
    base_url: str
    multi_agent_enabled: bool

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)


def load_siliconflow_settings() -> SiliconFlowSettings:
    load_dotenv()
    return SiliconFlowSettings(
        api_key=os.getenv("SILICONFLOW_API_KEY") or None,
        model=os.getenv("SILICONFLOW_MODEL") or None,
        base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
        multi_agent_enabled=os.getenv("SILICONFLOW_MULTI_AGENT_ENABLED", "false").casefold() == "true",
    )


def explain_negotiation(proposal_summary: str) -> str | None:
    """Reserved for a separately approved explanation feature.

    Negotiation must remain offline by default: it can run repeatedly while
    probing feasible options, so making external calls here would be costly and
    make the deterministic test/runtime path depend on provider availability.
    """
    return None


def extract_planning_constraints(query: str) -> dict[str, str] | None:
    """Extract only whitelisted numeric constraints from a free-text request.

    No health profile or recipe corpus is sent to the model. Its output remains
    a user-visible suggestion; submitted Pydantic data and the solver decide.
    """
    settings = load_siliconflow_settings()
    if not settings.configured:
        return None
    from openai import OpenAI

    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=10, max_retries=0)
    try:
        response = client.chat.completions.create(
            model=settings.model,
            temperature=0,
            max_tokens=160,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Extract only explicit meal-planning numbers. Return JSON with zero or more: max_total_minutes (integer), protein_min_g (number), energy_kcal_min (number), energy_kcal_max (number). Do not extract prices or budgets. Do not infer health targets, allergies, medical advice, or recipe choices."},
                {"role": "user", "content": query},
            ],
        )
        content = response.choices[0].message.content
        return _validate_constraint_payload(json.loads(content)) if content else None
    except (InvalidOperation, TypeError, ValueError, json.JSONDecodeError):
        return None
    except Exception:
        # Provider failures are fail-soft and never disclose credentials or provider details.
        return None


def _validate_constraint_payload(payload: Any) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    result: dict[str, str] = {}
    for key in ("protein_min_g",):
        if key in payload:
            value = Decimal(str(payload[key]))
            if value < 0:
                return None
            result[key] = str(value)
    if "max_total_minutes" in payload:
        value = int(str(payload["max_total_minutes"]))
        if value < 0:
            return None
        result["max_total_minutes"] = str(value)
    energy_keys = ("energy_kcal_min", "energy_kcal_max")
    if any(key in payload for key in energy_keys):
        if not all(key in payload for key in energy_keys):
            return None
        minimum = Decimal(str(payload["energy_kcal_min"]))
        maximum = Decimal(str(payload["energy_kcal_max"]))
        if minimum < 0 or maximum < minimum:
            return None
        result["energy_kcal_range"] = f"{minimum}-{maximum}"
    return result


def explain_verified_plan(summary: str) -> str | None:
    """Create wording only after deterministic validation has succeeded."""
    settings = load_siliconflow_settings()
    if not settings.configured or not settings.multi_agent_enabled:
        return None
    from openai import OpenAI

    try:
        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=10, max_retries=0)
        response = client.chat.completions.create(
            model=settings.model,
            temperature=0,
            max_tokens=220,
            messages=[
                {"role": "system", "content": "Write a concise Chinese meal-plan summary using only the verified facts provided. Do not make health, medical, allergy, or safety claims. Do not alter values or recommend unlisted foods."},
                {"role": "user", "content": summary},
            ],
        )
        return response.choices[0].message.content or None
    except Exception:
        return None

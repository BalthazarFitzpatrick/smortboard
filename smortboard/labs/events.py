"""read neutral events, including legacy logs normalized only at the adapter boundary"""

from typing import Any

from smortboard.labs.registry import get_adapter


def neutral_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    payload = event.get("payload") or {}
    if isinstance(payload.get("neutral"), list):
        return payload["neutral"]
    lab = payload.get("lab") or "anthropic"
    raw = {**payload, "type": payload.get("type") or event.get("kind")}
    return [
        {
            **item.to_dict(),
            "lab": lab,
            "profile": payload.get("profile") or "default",
            "model": item.model or payload.get("model"),
        }
        for item in get_adapter(lab).normalize(raw)
    ]


def result_fields(event: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (item["result"] for item in neutral_events(event) if item["kind"] == "result"), None
    )


def cost_sum(costs) -> float | None:
    values = list(costs)
    return None if any(value is None for value in values) else round(sum(values), 6)


def event_cost(event: dict[str, Any]) -> float | None:
    items = neutral_events(event)
    reported = next(
        (item["result"].get("cost_usd") for item in items if item["kind"] == "result"), None
    )
    if reported is not None:
        return float(reported)
    usage = [item["usage"].get("cost_usd") for item in items if item["kind"] == "usage"]
    return cost_sum(usage) if usage else None


def model_ref(item: dict[str, Any]) -> str | None:
    model = item.get("model")
    return f"{item.get('lab') or 'anthropic'}/{model}" if model else None

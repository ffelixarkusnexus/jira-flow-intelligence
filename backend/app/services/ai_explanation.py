"""AI explanation layer.

The AI is responsible ONLY for translating structured insights into
natural language. It MUST NOT compute, override, or invent numbers.

If no API key is configured, falls back to a deterministic template
that stitches the existing structured reasons into one sentence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.insight_service import BottleneckInsight

logger = get_logger(__name__)


def _template_explanation(insight: BottleneckInsight) -> str:
    if not insight.reasons:
        return f"{insight.status} appears to be the current bottleneck."
    joined = ", ".join(insight.reasons[:-1])
    if joined:
        joined = f"{joined}, and {insight.reasons[-1].lower()}"
    else:
        joined = insight.reasons[-1].lower()
    return (
        f"{insight.status} is currently the bottleneck "
        f"({insight.confidence.replace('_', ' ')} confidence). "
        f"Signals: {joined}."
    )


async def explain_insight(insight: BottleneckInsight, settings: Settings) -> str:
    fallback = _template_explanation(insight)

    if not settings.anthropic_api_key:
        return fallback

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.info("anthropic package not installed — returning template explanation.")
        return fallback

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    structured = {
        "status": insight.status,
        "score": insight.score,
        "confidence": insight.confidence,
        "reasons": insight.reasons,
    }
    system = (
        "You explain software delivery bottlenecks. "
        "Translate the structured input into ONE concise sentence. "
        "Rules: do NOT invent numbers, do NOT change meaning, be clear and actionable."
    )
    user = f"Data:\n{structured}\n\nOutput:"
    try:
        message = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Anthropic's `message.content` is a union of ~12 block types
        # (TextBlock, ToolUseBlock, ThinkingBlock, ...). Only TextBlock has
        # `.text`. We use getattr both for the type filter and the value
        # access so mypy doesn't try to narrow across the union.
        text = "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", "") == "text"
        ).strip()
        return text or fallback
    except Exception as exc:
        logger.warning("AI explanation failed (%s) — using template.", exc)
        return fallback

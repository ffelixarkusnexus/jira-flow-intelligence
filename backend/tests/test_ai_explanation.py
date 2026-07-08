"""AI explanation layer — verifies the deterministic template and the
no-key short-circuit. The actual Anthropic SDK call path is not exercised
here; integration with a real key is covered by manual smoke testing."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.ai_explanation import _template_explanation, explain_insight
from app.services.insight_service import BottleneckInsight


def _insight(reasons: list[str]) -> BottleneckInsight:
    return BottleneckInsight(
        status="Review",
        score=5,
        confidence="very_high",
        reasons=reasons,
        time_ratio=2.0,
        wip_ratio=1.5,
        throughput_delta=-0.3,
        current_avg_seconds=20000,
        previous_avg_seconds=10000,
        current_wip=15,
        previous_wip=10,
        current_throughput=7,
        previous_throughput=10,
    )


def test_template_with_multiple_reasons():
    text = _template_explanation(
        _insight(["Average time increased 100%", "WIP increased 50%", "Throughput decreased 30%"])
    )
    assert text.startswith("Review is currently the bottleneck")
    assert "very high" in text
    assert "average time increased 100%" in text.lower()
    assert "throughput decreased 30%" in text.lower()


def test_template_with_single_reason():
    text = _template_explanation(_insight(["Average time increased 100%"]))
    assert "average time increased 100%" in text.lower()


def test_template_with_no_reasons_falls_back_to_default():
    text = _template_explanation(_insight([]))
    assert "Review" in text
    assert "bottleneck" in text


@pytest.mark.asyncio
async def test_explain_uses_template_when_no_api_key():
    settings = Settings(anthropic_api_key="")
    text = await explain_insight(_insight(["Average time increased 100%"]), settings)
    assert "Review" in text
    assert "average time increased 100%" in text.lower()

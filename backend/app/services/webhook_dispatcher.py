"""Incoming-webhook dispatcher for Slack + Teams alert delivery (ADR-0037).

Both channels are the same mechanic: POST JSON to a customer-supplied incoming
webhook URL. Both accept a simple `{"text": ...}` body (Slack natively; Teams
incoming webhooks via the Connectors UI render the `text` field as markdown).
The per-channel markdown differences live in the message formatter
(alert_messages.py), not here — this module owns only the HTTP concern.

Never raises: failures are returned as DispatchResult so the dispatch loop can
record them (alert_fires) and move on. One retry on transient errors / 5xx; a
4xx (deleted Slack webhook, revoked Teams connector) is a hard failure surfaced
for the 24h failure digest.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# 30s covers Power Automate / Teams Workflows cold-start latency (the
# Workflows webhook is meaningfully slower than the deprecated Office 365
# Incoming Webhook connector - first invocation can take 10-20s before the
# runtime warms up). Slack hooks respond in <1s; the 30s ceiling is the
# absolute upper bound, not the typical wait. Verified 2026-06-01 against
# Microsoft Learn (`{"text": ...}` payload is correct per their docs; the
# bottleneck is purely the Workflows runtime).
_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    detail: str | None = None  # short failure reason, surfaced in the digest


def _teams_adaptive_card(text: str) -> dict[str, object]:
    """Wrap a text message in an Adaptive Card envelope. The current Teams
    Workflows webhook templates ("Send webhook alerts to a channel" etc.)
    explicitly require Adaptive Card or MessageCard format — the in-app
    Workflows help text says so verbatim, and a plain {"text": ...} payload
    times out (Power Automate slow-fails on validation). Verified 2026-06-01
    via the maintainer's setup screenshots."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                },
            }
        ],
    }


def post_webhook(url: str, text: str, *, channel_type: str = "slack") -> DispatchResult:
    """POST a text message to a Slack/Teams incoming webhook. `channel_type`
    selects payload shape: Slack accepts `{"text": ...}`; Teams Workflows
    requires Adaptive Card. Returns DispatchResult; never raises."""
    payload: dict[str, object] = (
        _teams_adaptive_card(text) if channel_type == "teams" else {"text": text}
    )
    last_detail = "unknown error"
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            last_detail = f"network error: {exc}"
            if attempt == 1:
                continue
            break
        if resp.status_code < 300:
            return DispatchResult(ok=True)
        last_detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        # 5xx is transient → retry once; 4xx is a hard failure (don't retry).
        if resp.status_code >= 500 and attempt == 1:
            continue
        break
    logger.warning("webhook_dispatch_failed", detail=last_detail)
    return DispatchResult(ok=False, detail=last_detail)

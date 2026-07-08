"""Tests for the Jira HTTP client using httpx.MockTransport.

We never hit a real Jira instance — the transport is replaced with a
deterministic responder that simulates pagination, 429 backoff, and 5xx retry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.config import Settings
from app.services.jira_client import JiraAuthError, JiraClient


def _settings(**overrides: object) -> Settings:
    base = {
        "jira_base_url": "https://example.atlassian.net",
        "jira_email": "bot@example.com",
        "jira_api_token": "token",
        "jira_page_size": 2,
        "jira_max_retries": 3,
        "jira_backoff_base_seconds": 0.0,  # no real sleeps in tests
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _collect(it: AsyncIterator[dict]) -> list[dict]:
    return [x async for x in it]


@pytest.mark.asyncio
async def test_search_paginates_until_total_reached() -> None:
    pages = [
        {
            "issues": [{"id": "1", "key": "A-1"}, {"id": "2", "key": "A-2"}],
            "total": 3,
        },
        {"issues": [{"id": "3", "key": "A-3"}], "total": 3},
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = JiraClient(_settings(), client=raw)
        results = await _collect(client.search_issues())

    assert [r["key"] for r in results] == ["A-1", "A-2", "A-3"]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds() -> None:
    state = {"attempts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["attempts"] += 1
        if state["attempts"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"issues": [{"id": "1", "key": "A-1"}], "total": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = JiraClient(_settings(), client=raw)
        results = await _collect(client.search_issues())

    assert state["attempts"] == 2
    assert results[0]["key"] == "A-1"


@pytest.mark.asyncio
async def test_retries_on_5xx_then_succeeds() -> None:
    state = {"attempts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["attempts"] += 1
        if state["attempts"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"issues": [], "total": 0})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = JiraClient(_settings(), client=raw)
        results = await _collect(client.search_issues())

    assert state["attempts"] == 2
    assert results == []


@pytest.mark.asyncio
async def test_raises_when_credentials_missing() -> None:
    settings = Settings(jira_base_url="https://x", jira_email="", jira_api_token="")
    async with JiraClient(settings) as client:
        with pytest.raises(JiraAuthError):
            await _collect(client.search_issues())


@pytest.mark.asyncio
async def test_raises_when_base_url_missing() -> None:
    settings = Settings(jira_base_url="", jira_email="x@x", jira_api_token="t")
    async with JiraClient(settings) as client:
        with pytest.raises(JiraAuthError):
            await _collect(client.search_issues())


@pytest.mark.asyncio
async def test_get_issue_changelog_paginates() -> None:
    pages = [
        {"values": [{"id": "1"}, {"id": "2"}], "total": 3},
        {"values": [{"id": "3"}], "total": 3},
    ]
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[n["i"]]
        n["i"] += 1
        return httpx.Response(200, json=page)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = JiraClient(_settings(), client=raw)
        result = await client.get_issue_changelog("ABC-1")

    assert len(result["histories"]) == 3


@pytest.mark.asyncio
async def test_eventually_gives_up_and_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = JiraClient(_settings(jira_max_retries=2), client=raw)
        with pytest.raises(RuntimeError, match="failed after"):
            await _collect(client.search_issues())

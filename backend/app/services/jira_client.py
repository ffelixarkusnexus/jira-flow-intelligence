import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class JiraAuthError(Exception):
    pass


class JiraClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    @property
    def _auth_header(self) -> dict[str, str]:
        if not self.settings.jira_email or not self.settings.jira_api_token:
            raise JiraAuthError("Jira credentials missing — set JIRA_EMAIL and JIRA_API_TOKEN.")
        token = base64.b64encode(
            f"{self.settings.jira_email}:{self.settings.jira_api_token}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "JiraClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        max_retries = self.settings.jira_max_retries
        backoff = self.settings.jira_backoff_base_seconds
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            try:
                response = await self._client.request(method, url, **kwargs)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", backoff * (2**attempt)))
                    logger.warning("Jira 429 — retrying in %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                if 500 <= response.status_code < 600:
                    sleep_for = backoff * (2**attempt)
                    logger.warning(
                        "Jira %d on %s — retry %d/%d after %.1fs",
                        response.status_code,
                        url,
                        attempt + 1,
                        max_retries,
                        sleep_for,
                    )
                    await asyncio.sleep(sleep_for)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                sleep_for = backoff * (2**attempt)
                logger.warning(
                    "Jira transport error on %s — retry %d/%d in %.1fs: %s",
                    url,
                    attempt + 1,
                    max_retries,
                    sleep_for,
                    exc,
                )
                await asyncio.sleep(sleep_for)

        raise RuntimeError(f"Jira request failed after {max_retries} retries: {last_exc}")

    async def search_issues(
        self,
        jql: str | None = None,
        fields: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.settings.jira_base_url:
            raise JiraAuthError("JIRA_BASE_URL not configured.")

        url = f"{self.settings.jira_base_url.rstrip('/')}/rest/api/3/search"
        page_size = self.settings.jira_page_size
        start_at = 0
        fields = fields or [
            "summary",
            "status",
            "created",
            "updated",
            "resolutiondate",
            "issuetype",
            "project",
        ]

        while True:
            params = {
                "jql": jql or self.settings.jira_jql,
                "expand": "changelog",
                "startAt": start_at,
                "maxResults": page_size,
                "fields": ",".join(fields),
            }
            response = await self._request_with_retry(
                "GET", url, params=params, headers=self._auth_header
            )
            data = response.json()
            issues = data.get("issues", [])
            if not issues:
                break
            for issue in issues:
                yield issue

            total = data.get("total", 0)
            start_at += len(issues)
            if start_at >= total or len(issues) < page_size:
                break

    async def get_issue_changelog(self, issue_id_or_key: str) -> dict[str, Any]:
        url = (
            f"{self.settings.jira_base_url.rstrip('/')}"
            f"/rest/api/3/issue/{issue_id_or_key}/changelog"
        )
        start_at = 0
        page_size = self.settings.jira_page_size
        all_histories: list[dict[str, Any]] = []

        while True:
            params = {"startAt": start_at, "maxResults": page_size}
            response = await self._request_with_retry(
                "GET", url, params=params, headers=self._auth_header
            )
            data = response.json()
            values = data.get("values", [])
            all_histories.extend(values)

            total = data.get("total", 0)
            start_at += len(values)
            if not values or start_at >= total:
                break

        return {"histories": all_histories}

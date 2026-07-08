"""Coverage for `app.services.resend_service` (ADR-0040 — customer-facing
transactional email via Resend, post-AWS-SES-denial).

Test seam mirrors the SES service convention: `_set_initialized_for_tests`
bypasses the Secrets Manager fetch; per-test `patch("resend.Emails.send",
...)` controls the SDK response. No network, no AWS, no Resend traffic.
Covers:

- successful send (per-path defaults + per-call override pattern)
- Resend SDK 4xx/5xx → False, no raise
- dry-run env var → True, no SDK call
- ses_enabled-equivalent (resend_enabled=False) → no-op skip
- secret-fetch failure paths via _ensure_initialized
- all four customer-facing public functions (paths #1-#4)
- fire_terminal_state_email dispatch logic (path #2 entry point)
- the helper functions (first_name_from_email, format_count, etc.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.db.models import Tenant
from app.services import resend_service
from app.services.resend_service import (
    ALERT_FROM_ADDRESS,
    BACKFILL_HARD_CAP,
    ResendConfigError,
    _classify_failure_mode,
    _first_name_from_email,
    _format_count,
    _set_initialized_for_tests,
    _site_name,
    fire_terminal_state_email,
    send_alert_email,
    send_backfill_cap_reached_email,
    send_backfill_completion_email,
    send_backfill_failure_email,
    send_failure_digest_email,
)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Force re-init between tests so module-level state doesn't leak.
    Also clear RESEND_DRY_RUN so a test that sets it can't bleed into
    the next one's assertions."""
    monkeypatch.delenv("RESEND_DRY_RUN", raising=False)
    _set_initialized_for_tests(False)
    yield
    _set_initialized_for_tests(False)


def _make_tenant(
    *,
    client_key: str = "t1",
    base_url: str = "https://acme.atlassian.net",
    display_url: str | None = "Acme Co",
    admin_contact_email: str | None = "casey.lee@acme.com",
    backfill_status: str | None = "completed",
    backfill_processed_issues: int | None = 1234,
    backfill_error: str | None = None,
) -> Tenant:
    return Tenant(
        client_key=client_key,
        cloud_id=f"{client_key}-cloud",
        base_url=base_url,
        display_url=display_url,
        product_type="jira",
        forge_installation_id=client_key,
        plan="free",
        enabled=True,
        installed_at=datetime(2026, 1, 1, tzinfo=UTC),
        admin_contact_email=admin_contact_email,
        backfill_status=backfill_status,
        backfill_processed_issues=backfill_processed_issues,
        backfill_error=backfill_error,
    )


def _enabled_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "resend_enabled": True,
        "resend_from_address": "notifications@example.com",
        "resend_reply_to": "support@example.com",
    }
    base.update(overrides)
    return Settings(**base)


# ----- _ensure_initialized (Secrets Manager fetch) -----------------------


def test_ensure_initialized_raises_when_arn_env_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESEND_API_KEY_SECRET_ARN", raising=False)
    with pytest.raises(ResendConfigError, match="RESEND_API_KEY_SECRET_ARN"):
        resend_service._ensure_initialized()


def test_ensure_initialized_raises_when_secret_value_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_API_KEY_SECRET_ARN", "arn:aws:secretsmanager:...:fake")
    fake_sm_client = MagicMock()
    fake_sm_client.get_secret_value.return_value = {"SecretString": ""}
    with (
        patch.object(resend_service.boto3, "client", return_value=fake_sm_client),
        patch.object(resend_service, "get_settings", return_value=_enabled_settings()),
        pytest.raises(ResendConfigError, match="empty"),
    ):
        resend_service._ensure_initialized()


def test_ensure_initialized_raises_on_cdk_placeholder_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the 2026-06-03 CDK fix: the Secret CDK provisions
    ships with a self-documenting placeholder ("REPLACE_WITH_..."). If
    the operator forgets to overwrite it before the deploy lands, the
    backend must refuse to use the placeholder rather than pass it to
    Resend (which would 401 with a less actionable error)."""
    monkeypatch.setenv("RESEND_API_KEY_SECRET_ARN", "arn:aws:secretsmanager:...:fake")
    fake_sm_client = MagicMock()
    fake_sm_client.get_secret_value.return_value = {
        "SecretString": "REPLACE_WITH_REAL_RESEND_API_KEY_VIA_AWS_CONSOLE"
    }
    with (
        patch.object(resend_service.boto3, "client", return_value=fake_sm_client),
        patch.object(resend_service, "get_settings", return_value=_enabled_settings()),
        pytest.raises(ResendConfigError, match="placeholder"),
    ):
        resend_service._ensure_initialized()


def test_ensure_initialized_raises_when_secrets_manager_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from botocore.exceptions import ClientError

    monkeypatch.setenv("RESEND_API_KEY_SECRET_ARN", "arn:aws:secretsmanager:...:fake")
    fake_sm_client = MagicMock()
    fake_sm_client.get_secret_value.side_effect = ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
        operation_name="GetSecretValue",
    )
    with (
        patch.object(resend_service.boto3, "client", return_value=fake_sm_client),
        patch.object(resend_service, "get_settings", return_value=_enabled_settings()),
        pytest.raises(ResendConfigError, match="Failed to fetch"),
    ):
        resend_service._ensure_initialized()


def test_ensure_initialized_populates_resend_api_key_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import resend as resend_sdk

    monkeypatch.setenv("RESEND_API_KEY_SECRET_ARN", "arn:aws:secretsmanager:...:fake")
    fake_sm_client = MagicMock()
    fake_sm_client.get_secret_value.return_value = {"SecretString": "re_secret123"}
    with (
        patch.object(resend_service.boto3, "client", return_value=fake_sm_client),
        patch.object(resend_service, "get_settings", return_value=_enabled_settings()),
    ):
        resend_service._ensure_initialized()
    assert resend_sdk.api_key == "re_secret123"
    # Second call must NOT re-fetch the secret (idempotent).
    fake_sm_client.get_secret_value.assert_called_once()
    resend_service._ensure_initialized()  # second call is a no-op
    fake_sm_client.get_secret_value.assert_called_once()


# ----- _send_email behavior ---------------------------------------------


def test_send_email_skips_when_resend_disabled() -> None:
    settings = _enabled_settings(resend_enabled=False)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send") as mock_send,
    ):
        ok = resend_service._send_email(to="a@b.com", subject="s", body_text="b")
    assert ok is False
    mock_send.assert_not_called()


def test_send_email_dry_run_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_DRY_RUN", "1")
    settings = _enabled_settings()
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send") as mock_send,
    ):
        ok = resend_service._send_email(to="a@b.com", subject="s", body_text="b")
    assert ok is True
    mock_send.assert_not_called()


def test_send_email_returns_false_when_secret_init_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operational realism: a misconfigured Secret should NOT throw out of
    the send call — it logs and returns False, matching SES behavior. The
    calling business operation (backfill, alert evaluation) shouldn't be
    aborted by an email-config bug."""
    monkeypatch.delenv("RESEND_API_KEY_SECRET_ARN", raising=False)
    settings = _enabled_settings()
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send") as mock_send,
    ):
        ok = resend_service._send_email(to="a@b.com", subject="s", body_text="b")
    assert ok is False
    mock_send.assert_not_called()


def test_send_email_success_calls_resend_with_correct_params() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "msg_abc"}) as mock_send,
    ):
        ok = resend_service._send_email(
            to="recipient@example.com",
            subject="Hello",
            body_text="Body content",
        )
    assert ok is True
    mock_send.assert_called_once()
    params = mock_send.call_args[0][0]
    assert params["from"] == "notifications@example.com"
    assert params["to"] == ["recipient@example.com"]
    assert params["subject"] == "Hello"
    assert params["text"] == "Body content"
    assert params["reply_to"] == ["support@example.com"]


def test_send_email_returns_false_on_resend_exception() -> None:
    """Any exception from the SDK is caught — return False, log, do not
    raise. The calling business operation continues."""
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", side_effect=RuntimeError("4xx from Resend")),
    ):
        ok = resend_service._send_email(to="x@y.com", subject="s", body_text="b")
    assert ok is False


def test_send_email_uses_per_call_from_and_reply_overrides() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        resend_service._send_email(
            to="recipient@example.com",
            subject="hi",
            body_text="body",
            from_address="notifications@example.com",
            reply_to="notifications@example.com",
        )
    params = mock_send.call_args[0][0]
    assert params["from"] == "notifications@example.com"
    assert params["reply_to"] == ["notifications@example.com"]


# ----- Public functions: per-path identity + payload assertions ---------


def test_send_alert_email_uses_alerts_from_address() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        send_alert_email(to="ops@acme.com", subject="Alert", body_text="Body")
    params = mock_send.call_args[0][0]
    assert params["from"] == ALERT_FROM_ADDRESS


def test_send_failure_digest_email_renders_lines_and_uses_default_from() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    failures = [
        {
            "destination_name": "#ops-alerts",
            "channel": "slack",
            "error_short": "404 channel_not_found",
            "last_failed_at_human": "10 minutes ago",
        },
        {
            "destination_name": "Teams ops",
            "channel": "teams",
            "error_short": "410 Gone",
            "last_failed_at_human": "2 hours ago",
        },
    ]
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        ok = send_failure_digest_email(
            to="admin@acme.com",
            admin_first_name="Alice",
            failure_count=2,
            failures=failures,
            project_dashboard_url="https://acme.atlassian.net/jira",
            pause_threshold=5,
        )
    assert ok is True
    params = mock_send.call_args[0][0]
    body = params["text"]
    subject = params["subject"]
    assert "Hi Alice" in body
    assert "#ops-alerts (slack)" in body
    assert "404 channel_not_found" in body
    assert "Teams ops (teams)" in body
    assert "Jira Flow Intelligence pauses it after 5 consecutive failures" in body
    assert "2 alert delivery failures" in subject
    assert params["from"] == "notifications@example.com"


# ----- Backfill path (#2) — admin-missing skip + content asserts --------


def test_completion_email_skipped_when_no_admin_contact() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    t = _make_tenant(admin_contact_email=None)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send") as mock_send,
    ):
        ok = send_backfill_completion_email(t)
    assert ok is False
    mock_send.assert_not_called()


def test_completion_email_body_includes_count_and_dashboard_link() -> None:
    """End-to-end content verification per the 2026-06-08 fix:

    The rendered email body's "Open it at" link must be the documented
    Forge deep-link form (per Atlassian docs:
    `/jira/{projectType}/projects/{key}/apps/{appId}/{envId}`) — one
    click lands the customer INSIDE Jira Flow Intelligence on the project, not
    on the tenant root or the project's Jira boards page.
    """
    settings = _enabled_settings()
    # Configure forge_app_id so url_helpers can construct the deep-link.
    settings.forge_app_id = "ari:cloud:ecosystem::app/00000000-0000-0000-0000-000000000000"
    _set_initialized_for_tests(True)
    t = _make_tenant(
        admin_contact_email="alice@acme.com",
        # ADR-0046 follow-up: customer-facing URLs are built from
        # display_url (user-facing) NOT base_url (canonical cloud-uuid).
        display_url="https://acme.atlassian.net",
        base_url="https://cloud-abc.atlassian.net",
        backfill_processed_issues=2_345,
    )
    # The resolver heartbeat populates forge_env_id on every dashboard
    # mount; set it here to exercise the steady-state deep-link path
    # (the post-resolver-heartbeat state every customer reaches within
    # seconds of clicking into Jira Flow Intelligence once).
    t.forge_env_id = "1a2b3c4d-5e6f-7890-abcd-ef0123456789"
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        # url_helpers reads get_settings via its own import; patch there too.
        patch("app.services.url_helpers.get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        ok = send_backfill_completion_email(t, project_key="DEMO")
    assert ok is True
    params = mock_send.call_args[0][0]
    assert params["to"] == ["alice@acme.com"]
    assert "2,345" in params["subject"]
    assert "Hi Alice" in params["text"]
    # The rendered URL must be the deep-link form — assertion-by-component:
    expected_url = (
        "https://acme.atlassian.net/jira/software/projects/DEMO/apps/"
        "00000000-0000-0000-0000-000000000000/"
        "1a2b3c4d-5e6f-7890-abcd-ef0123456789"
    )
    assert expected_url in params["text"], (
        f"completion email body must include the Forge deep-link URL — "
        f"the customer should land inside Jira Flow Intelligence in one click, not on "
        f"the project boards page or the tenant root. Expected substring: "
        f"{expected_url!r}; got body:\n{params['text']}"
    )
    # Regression guards for the 2026-06-08 bug surface:
    assert "cloud-" not in params["text"]  # canonical-uuid never leaks
    assert "/boards" not in params["text"]  # transitional fallback NOT used


def test_failure_email_picks_failure_sentence_by_mode() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    t = _make_tenant(
        admin_contact_email="bob@acme.com",
        backfill_status="failed",
        backfill_error="HTTP 429 rate limited",
    )
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        ok = send_backfill_failure_email(t, t.backfill_error)
    assert ok is True
    params = mock_send.call_args[0][0]
    assert "rate-limited" in params["text"]
    assert "Acme Co" in params["subject"]


def test_failure_email_falls_through_to_unknown_when_error_unclassifiable() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    t = _make_tenant(
        admin_contact_email="carol@acme.com",
        backfill_status="failed",
        backfill_error=None,
    )
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        send_backfill_failure_email(t, None)
    body = mock_send.call_args[0][0]["text"]
    assert "unexpected error occurred" in body


def test_cap_reached_email_subject_and_body() -> None:
    settings = _enabled_settings()
    _set_initialized_for_tests(True)
    t = _make_tenant(admin_contact_email="dave@acme.com", backfill_processed_issues=50000)
    with (
        patch.object(resend_service, "get_settings", return_value=settings),
        patch("resend.Emails.send", return_value={"id": "x"}) as mock_send,
    ):
        ok = send_backfill_cap_reached_email(t)
    assert ok is True
    params = mock_send.call_args[0][0]
    assert "50,000" in params["subject"]
    assert "50,000 historical issues" in params["text"]


# ----- fire_terminal_state_email dispatch --------------------------------


def test_fire_terminal_dispatches_completion_under_cap() -> None:
    t = _make_tenant(backfill_status="completed", backfill_processed_issues=BACKFILL_HARD_CAP - 1)
    with (
        patch.object(resend_service, "send_backfill_completion_email") as completion,
        patch.object(resend_service, "send_backfill_cap_reached_email") as cap,
        patch.object(resend_service, "send_backfill_failure_email") as failure,
    ):
        # No db passed → project_key resolution is skipped (defaults to
        # None). The router-side caller passes db; this test exercises
        # the test-path backward-compat branch.
        fire_terminal_state_email(t)
    completion.assert_called_once_with(t, project_key=None)
    cap.assert_not_called()
    failure.assert_not_called()


def test_fire_terminal_dispatches_cap_at_or_above_cap() -> None:
    t = _make_tenant(backfill_status="completed", backfill_processed_issues=BACKFILL_HARD_CAP)
    with (
        patch.object(resend_service, "send_backfill_completion_email") as completion,
        patch.object(resend_service, "send_backfill_cap_reached_email") as cap,
    ):
        fire_terminal_state_email(t)
    cap.assert_called_once_with(t)
    completion.assert_not_called()


def test_fire_terminal_dispatches_failure_on_failed_status() -> None:
    t = _make_tenant(backfill_status="failed", backfill_error="something")
    with patch.object(resend_service, "send_backfill_failure_email") as failure:
        fire_terminal_state_email(t)
    failure.assert_called_once_with(t, "something", project_key=None)


def test_fire_terminal_is_noop_on_non_terminal_status() -> None:
    t = _make_tenant(backfill_status="running")
    with (
        patch.object(resend_service, "send_backfill_completion_email") as completion,
        patch.object(resend_service, "send_backfill_failure_email") as failure,
        patch.object(resend_service, "send_backfill_cap_reached_email") as cap,
    ):
        fire_terminal_state_email(t)
    completion.assert_not_called()
    failure.assert_not_called()
    cap.assert_not_called()


# ----- helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("casey.lee@example.com", "Casey"),
        ("admin@foo.com", "Admin"),
        ("UPPER@foo.com", "Upper"),
        ("", "there"),
        (None, "there"),
        ("@nothing.com", "there"),
    ],
)
def test_first_name_from_email(email: str | None, expected: str) -> None:
    assert _first_name_from_email(email) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0"), (None, "0"), (1234, "1,234"), (50000, "50,000")],
)
def test_format_count(n: int | None, expected: str) -> None:
    assert _format_count(n) == expected


def test_site_name_prefers_display_url() -> None:
    assert _site_name(_make_tenant(display_url="Acme Co")) == "Acme Co"


def test_site_name_strips_protocol_from_base_url_when_display_unset() -> None:
    t = _make_tenant(display_url=None, base_url="https://acme.atlassian.net/")
    assert _site_name(t) == "acme.atlassian.net"


def test_site_name_falls_back_when_both_unset() -> None:
    assert _site_name(_make_tenant(display_url=None, base_url="")) == "your site"


# `_dashboard_link` was removed in the 2026-06-08 customer-facing URL
# routing fix. Its semantics moved to `app.services.url_helpers.
# project_dashboard_url(tenant, project_key)` which is exhaustively
# tested in `backend/tests/test_url_helpers.py` — including the
# trailing-slash hygiene the old `_dashboard_link` tests pinned. The
# display_url-first preference + the `cloud-{uuid}` regression guard
# also live there.


@pytest.mark.parametrize(
    ("error", "mode"),
    [
        (None, "unknown"),
        ("", "unknown"),
        ("HTTP 429 too many requests", "rate_limit"),
        ("401 unauthorized", "auth"),
        ("500 internal server error", "jira_5xx"),
        ("connection timeout to jira", "network"),
        ("Forge consumer queue overflow", "platform"),
        ("Something weird happened", "unknown"),
    ],
)
def test_classify_failure_mode(error: str | None, mode: str) -> None:
    assert _classify_failure_mode(error) == mode

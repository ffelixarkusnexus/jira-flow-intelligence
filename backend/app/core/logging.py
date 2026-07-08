"""Structured logging configuration.

Production: JSON output. Each log line is a single JSON object that
CloudWatch Logs Insights can query by field. Example:

    {"event": "sync_completed", "tenant": "acme", "issues": 200,
     "level": "info", "timestamp": "2026-05-07T01:23:45Z"}

Dev / tests: ConsoleRenderer with key=value pairs and colored level —
much easier to read in a terminal.

The formatter is selected via `STRUCTLOG_JSON=1` env var. App Runner
sets it via CDK; local dev leaves it unset.

Existing code keeps working through structlog's stdlib bridge. A call
like `logger.info("Synced %d issues", n)` (printf-style) still emits
correctly. New code should prefer the kwargs style:
`logger.info("sync_completed", issues=n, tenant=ctx.tenant_id)`.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(*, json_format: bool | None = None, level: int = logging.INFO) -> None:
    """Configure both stdlib logging AND structlog. Idempotent — calling
    twice is safe (structlog.configure replaces the prior config)."""
    if json_format is None:
        json_format = os.getenv("STRUCTLOG_JSON") == "1"

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.JSONRenderer()
            if json_format
            else structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib loggers (FastAPI, SQLAlchemy, uvicorn, etc.) through
    # the same renderer so prod logs are uniform JSON.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(message)s")
        if json_format
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    )
    root = logging.getLogger()
    # Replace any prior handlers (basicConfig adds one) with our single one.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Drop-in replacement for `logging.getLogger(__name__)`."""
    return structlog.get_logger(name)

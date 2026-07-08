"""Static lint on Alembic migrations to catch dialect-portability bugs.

Catches the class of bug from `4199546` (work_schedules.enabled used
`server_default=sa.text("0")` which SQLite accepted but Postgres
rejected with `column "enabled" is of type boolean but default
expression is of type integer`).

Runs as a normal pytest — the CI `backend-postgres (smoke)` job is the
load-bearing regression gate but it runs too late (post-push, against a
real Postgres instance). This test runs in the same sqlite-only pytest
sweep the developer runs pre-push, catching the pattern at write time.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Match `sa.Column(..., sa.Boolean(), ..., server_default=...)`. The
# pattern is intentionally permissive — it locates Boolean column blocks
# whose `server_default` literal contains a digit-only string (the bug
# shape: `sa.text("0")`, `sa.text("1")`, `"0"`, `"1"`, etc.).
#
# Legal Boolean defaults: `sa.false()`, `sa.true()`, `sa.text("false")`,
# `sa.text("true")`. These do not match the integer-literal pattern.
_BOOL_COL_PATTERN = re.compile(
    r"sa\.Column\(\s*"
    r'"[^"]+"\s*,\s*'  # column name
    r"sa\.Boolean\(\)"  # type marker
    r".*?"  # any intermediate args (nullable=False, etc.)
    r"server_default\s*=\s*"
    r"(?P<default>"
    r"sa\.text\s*\([^)]+\)"  # sa.text("...")
    r"|sa\.true\s*\(\s*\)"  # sa.true()
    r"|sa\.false\s*\(\s*\)"  # sa.false()
    r"|\"[^\"]+\""  # bare string literal
    r"|'[^']+'"  # bare single-quoted string
    r"|\d+"  # bare integer
    r")",
    re.DOTALL,
)


def _is_integer_default(literal: str) -> bool:
    """Return True if the server_default literal is an integer-shaped
    expression that Postgres will reject for a BOOLEAN column.

    Examples that should match (and FAIL the lint):
      - sa.text("0"), sa.text("1")
      - "0", "1"
      - sa.text('0')
      - 0, 1 (raw integers — also reject for BOOLEAN)

    Examples that should NOT match:
      - sa.false(), sa.true()
      - sa.text("false"), sa.text("true")
    """
    s = literal.strip()
    # Strip the sa.text() wrapper if present.
    m = re.match(r"sa\.text\(\s*['\"]([^'\"]+)['\"]\s*\)", s)
    if m:
        s = m.group(1)
    s = s.strip().strip("'\"")
    return s.isdigit()


def test_no_boolean_columns_use_integer_server_default():
    """For every migration in backend/alembic/versions/, every Boolean
    column with a `server_default` uses a portable Boolean literal
    (sa.false(), sa.true(), or sa.text("false")/sa.text("true")) — never
    an integer literal that Postgres rejects."""
    bad: list[tuple[str, str, str]] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text()
        for match in _BOOL_COL_PATTERN.finditer(text):
            default = match.group("default").strip()
            if _is_integer_default(default):
                # Capture a snippet of the column declaration for the failure.
                snippet = match.group(0)[:200].replace("\n", " ")
                bad.append((path.name, default, snippet))

    assert not bad, (
        "Boolean columns must use portable boolean defaults "
        "(sa.false() / sa.true() / sa.text('false') / sa.text('true')). "
        "Integer literals are rejected by Postgres for BOOLEAN columns. "
        f"Offending migrations: {bad}"
    )


def test_lint_correctly_detects_the_4199546_bug_pattern():
    """Sanity check: the pattern that originally shipped (and was caught
    by CI's postgres smoke test) is detected by this lint. Asserts the
    matcher is wired correctly so future regressions of the same shape
    don't slip through."""
    fake_migration = """
        op.add_column(
            "fake_table",
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    """
    m = _BOOL_COL_PATTERN.search(fake_migration)
    assert m is not None, "regex must locate the Boolean+server_default block"
    assert _is_integer_default(m.group("default")), (
        "the original 4199546 bug pattern (sa.text('0')) must be classified as integer"
    )


def test_lint_accepts_the_4199546_fix_pattern():
    """And the fix (sa.false()) must NOT be flagged."""
    fake_migration = """
        op.add_column(
            "fake_table",
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    """
    m = _BOOL_COL_PATTERN.search(fake_migration)
    assert m is not None
    assert not _is_integer_default(m.group("default")), (
        "sa.false() must NOT be classified as an integer default"
    )

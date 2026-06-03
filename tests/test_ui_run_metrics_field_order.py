"""Run Metrics card — two-column field ordering (Plan Phase 2).

The per-phase "Run Metrics" detail card in ui/index.html groups its fields into
two columns. The requested layout is:
    LEFT  : Duration, Skill, Cost
    RIGHT : Exec attempts, Reviewer passes, Escalations

These are static source-order checks (the single-file React app is transpiled
in-browser; there is no JS test runner). The card is rendered as two explicit
column <div>s with the LEFT column emitted first in source, so source order
mirrors the on-screen left→right, top→bottom reading order. Every lookup is
scoped to the Run Metrics block — labels like "Duration:" also appear elsewhere
(e.g. the escalation-log tab), so an unscoped search would be ambiguous.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _run_metrics_block(html: str) -> str:
    """Slice the Run Metrics card: from its 'Run Metrics:' label up to the
    'Git branch reference' comment that immediately follows the card grid."""
    start = html.index("Run Metrics:")
    end = html.index("Git branch reference", start)
    return html[start:end]


def test_run_metrics_left_column_order():
    """LEFT column renders Duration → Skill → Cost, in that source order."""
    block = _run_metrics_block(_html())
    assert block.index("Duration:") < block.index("Skill:") < block.index("Cost:")


def test_run_metrics_right_column_order():
    """RIGHT column renders Exec attempts → Reviewer passes → Escalations."""
    block = _run_metrics_block(_html())
    assert (
        block.index("Exec attempts:")
        < block.index("Reviewer passes:")
        < block.index("Escalations:")
    )


def test_run_metrics_left_column_precedes_right_column():
    """The entire LEFT column is emitted before the RIGHT column in source.

    Discriminating assertion (red against the old layout): the old row-major grid
    emitted Cost last and Exec attempts second, so index('Cost:') < index('Exec
    attempts:') was False. With two explicit columns (left first) it is True. This
    is what guarantees the user-visible columns are Duration/Skill/Cost on the
    left and Exec attempts/Reviewer passes/Escalations on the right.
    """
    block = _run_metrics_block(_html())
    assert block.index("Cost:") < block.index("Exec attempts:")

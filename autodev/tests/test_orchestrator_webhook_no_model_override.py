"""Regression: orchestrator must not pass model= to invoke_agent_webhook (OpenClaw agent defaults)."""

import os
import re


def _iter_invoke_agent_webhook_calls(source: str):
    """Yield each full ``invoke_agent_webhook(...)`` call span (handles multiline)."""
    for m in re.finditer(r"invoke_agent_webhook\s*\(", source):
        start = m.start()
        i = m.end()  # position after '('
        depth = 1
        while i < len(source) and depth > 0:
            ch = source[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        yield source[start:i]


def test_orchestrator_never_passes_model_kwarg_to_invoke_agent_webhook():
    pipeline_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "autodev",
        "pipeline",
    )
    path = os.path.join(pipeline_dir, "orchestrator.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    bad = []
    for call in _iter_invoke_agent_webhook_calls(src):
        if re.search(r"\bmodel\s*=", call):
            bad.append(call[:200] + ("..." if len(call) > 200 else ""))
    assert not bad, (
        "invoke_agent_webhook must not receive model= — use OpenClaw agents.list model:\n"
        + "\n---\n".join(bad)
    )

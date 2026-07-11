"""Regression: webhook ``model=`` may come only from the per-phase override.

The three phase invoke sites (planner/executor/reviewer) thread
``model=self._phase_model_override("<role>")`` (the dashboard's one-phase
override); with no override set the kwarg is None and the payload omits it, so
OpenClaw keeps using each agent's ``agents.list`` model. No other call site
(escalation, the post-run completion reviewer) may carry ``model=``.
"""

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


def _orchestrator_source() -> str:
    pipeline_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "autodev",
        "pipeline",
    )
    with open(os.path.join(pipeline_dir, "orchestrator.py"), encoding="utf-8") as f:
        return f.read()


def test_model_kwarg_comes_only_from_phase_override():
    src = _orchestrator_source()
    bad = []
    override_sites = set()
    for call in _iter_invoke_agent_webhook_calls(src):
        if not re.search(r"\bmodel\s*=", call):
            continue
        first_arg = re.search(r'invoke_agent_webhook\s*\(\s*"([\w-]+)"', call)
        agent = first_arg.group(1) if first_arg else None
        shaped = agent in ("planner", "executor", "reviewer") and re.search(
            r'model\s*=\s*self\._phase_model_override\(\s*"' + re.escape(agent) + r'"\s*\)',
            call,
        )
        if shaped:
            override_sites.add(agent)
        else:
            bad.append(call[:200])
    assert not bad, (
        "model= must be exactly self._phase_model_override(<matching role>) and only "
        "on the phase invoke sites:\n" + "\n---\n".join(bad)
    )
    assert override_sites == {"planner", "executor", "reviewer"}, (
        f"expected the three phase invoke sites to thread the override, got {sorted(override_sites)}"
    )

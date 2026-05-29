"""Surface a stranded chat reply: turns/{n}.md written but turns/{n}.done missing.

Observed live (Balloon Popping Game, session-24): the agent wrote the reply
(`turns/24.md`, 1905 bytes) but `turns/24.done` never landed (interrupted run).
The completion contract hangs entirely on the `.done` sentinel, so the held
POST waited the full backstop and `GET /session` left the placeholder
`pending` forever — the reply was stranded on disk, invisible without (and
even with) a refresh.

Fix: treat a fresh `.md` whose activity stamp has gone silent for `quiet_secs`
as a completion — the agent has demonstrably stopped, so the `.md` is final.
The stamp-silence gate is what prevents prematurely surfacing the chat reply
mid-PRD-write (when the stamp is still being touched), so normal long turns are
unaffected. Used in both the poll (stalled/timeout branches) and the
`GET /session` reconciliation (so a refresh / the recovery poll surfaces it).
"""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from autodev.pipeline.sentinel_poller import PollResult


def _mk_idea(tmp_path):
    d = tmp_path / "idea"
    (d / "turns").mkdir(parents=True)
    return d


def _write(p: Path, text: str, mtime: float | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


# ── unit: _ideas_stranded_md_reply ───────────────────────────────────────────

def test_stranded_returns_none_when_done_exists(tmp_path):
    from ui.server import _ideas_stranded_md_reply
    d = _mk_idea(tmp_path)
    now = time.time()
    _write(d / "turns" / "1.md", "the reply", now - 400)
    _write(d / "turns" / "1.done", "done", now - 390)
    # .done present → the normal path owns this; stranded helper must defer.
    assert _ideas_stranded_md_reply(d, 1, now - 500, 300.0) is None


def test_stranded_returns_md_when_stamp_silent_long_enough(tmp_path):
    from ui.server import _ideas_stranded_md_reply
    d = _mk_idea(tmp_path)
    now = time.time()
    _write(d / "turns" / "1.md", "stranded reply text", now - 400)  # fresh for attempt
    _write(d / "prd_creator_activity.stamp", "", now - 400)  # agent silent 400s >= 300
    out = _ideas_stranded_md_reply(d, 1, attempt_start=now - 500, quiet_secs=300.0)
    assert out == "stranded reply text"


def test_stranded_returns_none_when_stamp_recently_active(tmp_path):
    """Agent may still be working (e.g. a long silent PRD-draft model call):
    the .md exists but the stamp was touched recently → do NOT surface yet."""
    from ui.server import _ideas_stranded_md_reply
    d = _mk_idea(tmp_path)
    now = time.time()
    _write(d / "turns" / "1.md", "partial maybe", now - 200)
    _write(d / "prd_creator_activity.stamp", "", now - 30)  # active 30s ago < 300
    assert _ideas_stranded_md_reply(d, 1, now - 500, 300.0) is None


def test_stranded_returns_none_for_stale_md_from_prior_attempt(tmp_path):
    from ui.server import _ideas_stranded_md_reply
    d = _mk_idea(tmp_path)
    now = time.time()
    # .md mtime well before this attempt started → leftover from a prior turn.
    _write(d / "turns" / "1.md", "old reply", now - 1000)
    _write(d / "prd_creator_activity.stamp", "", now - 400)
    assert _ideas_stranded_md_reply(d, 1, attempt_start=now - 500, quiet_secs=300.0) is None


def test_stranded_returns_none_when_no_md(tmp_path):
    from ui.server import _ideas_stranded_md_reply
    d = _mk_idea(tmp_path)
    now = time.time()
    _write(d / "prd_creator_activity.stamp", "", now - 400)
    assert _ideas_stranded_md_reply(d, 1, now - 500, 300.0) is None


# ── poll: stalled branch surfaces a stranded reply instead of failing ────────

def _poll():
    from ui.server import _poll_sentinel_with_idle_detect
    return _poll_sentinel_with_idle_detect


def test_poll_returns_succeeded_when_stalled_but_md_present(tmp_path):
    """Stamp went silent past stall_threshold (agent stopped) but left a fresh
    .md with no .done → the poll must surface the reply (succeeded), not stalled."""
    d = _mk_idea(tmp_path)
    done = d / "turns" / "1.done"      # never created
    stamp = d / "prd_creator_activity.stamp"
    md = d / "turns" / "1.md"
    attempt_start = time.time() - 10.0
    _write(stamp, "", attempt_start + 0.1)          # one early activity, then silent
    _write(md, "agent reply on disk", attempt_start + 0.2)  # fresh .md, agent left it

    result = asyncio.run(_poll()(
        done_path=done,
        stamp_path=stamp,
        attempt_start_wall=attempt_start,
        poll_timeout=900.0,
        poll_interval=0.01,
        stall_threshold=1.0,     # silence (now - 0.1) >> 1.0 → stalled branch fires immediately
        startup_grace=600.0,
    ))
    assert isinstance(result, PollResult)
    assert result.success is True, f"expected succeeded via stranded .md, got {result.reason}"
    assert result.reason == "succeeded"


def test_poll_still_stalls_when_no_md(tmp_path):
    """Stamp silent + NO .md (agent produced nothing) → still stalled."""
    d = _mk_idea(tmp_path)
    done = d / "turns" / "1.done"
    stamp = d / "prd_creator_activity.stamp"
    attempt_start = time.time() - 10.0
    _write(stamp, "", attempt_start + 0.1)  # silent, no .md
    result = asyncio.run(_poll()(
        done_path=done,
        stamp_path=stamp,
        attempt_start_wall=attempt_start,
        poll_timeout=900.0,
        poll_interval=0.01,
        stall_threshold=1.0,
        startup_grace=600.0,
    ))
    assert result.success is False
    assert result.reason == "stalled"


# ── reconciliation: heal a PENDING placeholder from a stranded .md ───────────

def _session_with_pending(turn_n, attempt_start):
    return {
        "name": "Stranded",
        "messages": [
            {"role": "user", "content": "make it number blocker themed", "ideas_turn": turn_n, "attempt_start_wall": attempt_start},
            {"role": "assistant", "content": "Working on your request...", "pending": True},
        ],
        "prd_content": "",
        "annotations": [],
    }


def test_reconcile_heals_pending_placeholder_from_stranded_md(tmp_path):
    from ui.server import _reconcile_ideas_session_after_late_done
    d = _mk_idea(tmp_path)
    now = time.time()
    attempt_start = now - 500
    _write(d / "turns" / "1.md", "Here is your number-blocker themed reply.", now - 400)
    _write(d / "prd_creator_activity.stamp", "", now - 400)  # silent 400s
    session = _session_with_pending(1, attempt_start)
    out, changed = _reconcile_ideas_session_after_late_done(d, session, quiet_secs=300.0)
    assert changed is True
    last = out["messages"][-1]
    assert last["role"] == "assistant"
    assert last["pending"] is False
    assert last.get("error") in (False, None)
    assert "number-blocker" in last["content"]


def test_reconcile_does_not_heal_pending_while_stamp_active(tmp_path):
    """Normal in-flight turn: .md may exist but the stamp is recently active →
    must NOT prematurely resolve the pending bubble."""
    from ui.server import _reconcile_ideas_session_after_late_done
    d = _mk_idea(tmp_path)
    now = time.time()
    attempt_start = now - 60
    _write(d / "turns" / "1.md", "early chat prose", now - 40)
    _write(d / "prd_creator_activity.stamp", "", now - 5)  # active 5s ago
    session = _session_with_pending(1, attempt_start)
    out, changed = _reconcile_ideas_session_after_late_done(d, session, quiet_secs=300.0)
    assert changed is False
    assert out["messages"][-1]["pending"] is True

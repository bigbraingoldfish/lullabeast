"""Part B2 — POST /api/escalation/inbound (inbound operator-reply path).

An OpenClaw inbound hook forwards an operator's channel reply here (authenticated
by the hooks shared secret). The reply is mapped to a pipeline command and written
through the SAME files the dashboard's POST /api/command writes, so the orchestrator
consumer needs no change. The UI server stays the only writer of
escalation_output / pending_escalation_command; the escalation agent never applies
commands.

Project-boundedness (B0): the reply is routed to the project that ESCALATED via the
correlation token in that project's phase_state.json, and the command is written to
THAT project's directory by absolute path — never the live symlink / active project.

Covers: source-param refactor on the writers; reply-text->verb mapping; server-side
channel resolution + best-effort ack; auth (hooks token, middleware exemption);
token->project routing (+ single-parked fallback, ambiguous reject, stale reject);
active-vs-parked write-path selection; reset-cap rejection.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import ui.server as srv


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_lifespan():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_context(app):
        yield

    return mock_context


@pytest.fixture
def test_client(mock_lifespan):
    srv.app.router.lifespan_context = mock_lifespan
    with TestClient(srv.app) as client:
        yield client


def _art(project_dir: str) -> str:
    return os.path.join(project_dir, ".autodev", "pipeline")


def _make_project(tmp: Path, name: str, *, token=None, escalation_resets=0, nuclear_resets=0) -> str:
    pdir = tmp / name
    art = Path(_art(str(pdir)))
    art.mkdir(parents=True, exist_ok=True)
    ps = {}
    if token is not None:
        ps["escalation_reply_token"] = token
    if escalation_resets:
        ps["escalation_resets"] = escalation_resets
    if nuclear_resets:
        ps["nuclear_resets"] = nuclear_resets
    (art / "phase_state.json").write_text(json.dumps(ps))
    return str(pdir)


def _entry(project_path: str, state: str, *, position=0, parked_status="WAITING_FOR_HUMAN", eid=None):
    e = {"id": eid or f"e-{os.path.basename(project_path)}", "name": os.path.basename(project_path),
         "project_path": project_path, "state": state, "position": position}
    if state in ("ESCALATION", "ESCALATION_ANSWERED"):
        e["parked_pipeline_status"] = parked_status
    return e


def _scaffold(tmp: Path, *, active_project=None, active_status="RUNNING", queue_entries=None, hooks_token="hooktok", **extra):
    qp = tmp / "pipeline_queue.json"
    qp.write_text(json.dumps({"queue": queue_entries or [], "queue_mode": "auto", "last_updated": ""}))
    sp = tmp / "pipeline_state.json"
    sp.write_text(json.dumps({"pipeline_status": active_status,
                              "project_path": active_project or ""}))
    cfg = {
        "project_dir_path": active_project,
        "pipeline_state_path": str(sp),
        "phase_state_path": os.path.join(active_project, ".autodev", "pipeline", "phase_state.json") if active_project else None,
        "pipeline_queue_path": str(qp),
        "hooks_token": hooks_token,
        "hooks_url": "http://localhost:18789/hooks/agent",
        "events_path": str(tmp / "events.jsonl"),
    }
    cfg.update(extra)
    return cfg


_AUTH = {"Authorization": "Bearer hooktok"}


# ---------------------------------------------------------------------------
# source-param refactor on the writers (existing callers stay "ui")
# ---------------------------------------------------------------------------

def test_write_escalation_files_default_source_is_ui(tmp_path):
    srv._write_escalation_files(str(tmp_path), "RETRY")
    data = json.loads((Path(_art(str(tmp_path))) / "escalation_output.json").read_text())
    assert data["source"] == "ui"


def test_write_escalation_files_accepts_source_inbound(tmp_path):
    srv._write_escalation_files(str(tmp_path), "RETRY", source="inbound")
    data = json.loads((Path(_art(str(tmp_path))) / "escalation_output.json").read_text())
    assert data["source"] == "inbound" and data["command"] == "RETRY"


def test_write_pending_escalation_files_default_source_is_ui(tmp_path):
    srv._write_pending_escalation_files(str(tmp_path), "PROCEED")
    data = json.loads((Path(_art(str(tmp_path))) / "pending_escalation_command.json").read_text())
    assert data["source"] == "ui"


def test_write_pending_escalation_files_accepts_source_inbound(tmp_path):
    srv._write_pending_escalation_files(str(tmp_path), "PROCEED", source="inbound")
    data = json.loads((Path(_art(str(tmp_path))) / "pending_escalation_command.json").read_text())
    assert data["source"] == "inbound" and data["command"] == "PROCEED"


# ---------------------------------------------------------------------------
# reply-text -> command verb mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,verb", [
    ("retry", "RETRY"),
    ("please resume", "RETRY"),
    ("reset phase", "RESET_PHASE"),
    ("restart phase", "RESET_PHASE"),
    ("reset execution", "RESET_EXECUTION"),
    ("reset reviewer", "RESET_REVIEWER"),
    ("proceed", "PROCEED"),
    ("continue", "PROCEED"),
    ("go ahead", "PROCEED"),
    ("stop", "STOP"),
    ("halt", "STOP"),
    ("i want to stop", "STOP"),          # "want" must not read as a negation ("n't")
    ("retry — the executor did not crash", "RETRY"),  # negation AFTER the verb is harmless
    ("skip", "SKIP"),
    ("nuclear reset", "NUCLEAR_RESET"),
])
def test_text_to_command_maps_known_verbs(text, verb):
    assert srv._inbound_text_to_command(text) == verb


@pytest.mark.parametrize("text", [
    "",
    "hello there",
    "reset",                 # ambiguous: which reset?
    "stop or proceed",       # two distinct verbs
    "i think we should think about it",
])
def test_text_to_command_unrecognized_or_ambiguous_is_none(text):
    assert srv._inbound_text_to_command(text) is None


@pytest.mark.parametrize("text", [
    "don't stop the pipeline",   # negation immediately before the verb
    "do not proceed",
    "please don't reset phase",
    "cannot continue right now",
    "let's not retry",
    "go look at the logs first",  # bare "go" is no longer a PROCEED trigger
])
def test_text_to_command_negation_or_loose_prose_is_none(text):
    # A verb negated by a preceding "not"/"don't"/… must NOT fire — the endpoint
    # asks for clarification instead of issuing a (potentially destructive) command.
    assert srv._inbound_text_to_command(text) is None


def test_text_to_command_strips_leading_correlation_token():
    # the operator starts the reply with the token; it must not false-match a verb
    assert srv._inbound_text_to_command("e1.ab12cd RESET_PHASE") == "RESET_PHASE"


def test_text_to_command_skip_only_on_explicit():
    # a generic reply never becomes SKIP
    assert srv._inbound_text_to_command("not sure, maybe later") is None


# ---------------------------------------------------------------------------
# server-side channel resolver + best-effort ack
# ---------------------------------------------------------------------------

def test_resolve_channel_server_explicit_and_binding_and_none():
    assert srv._resolve_notification_channel_server({"notification_channel": "telegram"}) == "telegram"
    assert srv._resolve_notification_channel_server(
        {"bindings": [{"agentId": "escalation", "match": {"channel": "signal"}}]}) == "signal"
    assert srv._resolve_notification_channel_server({}) is None


def test_ack_skips_post_when_channel_unresolved(monkeypatch):
    posted = []

    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): posted.append((a, k))

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: _FakeSession())
    asyncio.run(srv._post_inbound_ack({"hooks_url": "u", "hooks_token": "t"}, "hi"))
    assert posted == []


def test_ack_posts_resolved_channel(monkeypatch):
    posted = []

    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): posted.append((a, k))

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: _FakeSession())
    cfg = {"hooks_url": "u", "hooks_token": "t",
           "bindings": [{"agentId": "escalation", "match": {"channel": "signal"}}]}
    asyncio.run(srv._post_inbound_ack(cfg, "hi"))
    assert posted and posted[0][1]["json"]["channel"] == "signal"


# ---------------------------------------------------------------------------
# Endpoint — auth
# ---------------------------------------------------------------------------

def test_inbound_401_on_missing_or_wrong_hooks_token(test_client, tmp_path):
    cfg = _scaffold(tmp_path)
    with patch("ui.server.load_config", return_value=cfg):
        assert test_client.post("/api/escalation/inbound", json={"text": "retry"}).status_code == 401
        assert test_client.post("/api/escalation/inbound", json={"text": "retry"},
                                headers={"Authorization": "Bearer WRONG"}).status_code == 401


def test_inbound_503_when_no_hooks_token_configured(test_client, tmp_path):
    cfg = _scaffold(tmp_path, hooks_token="")
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound", json={"text": "retry"}, headers=_AUTH)
        assert r.status_code == 503


def test_inbound_exempt_from_ui_token_middleware_but_self_authenticates(test_client, tmp_path):
    """The endpoint is exempt from the UI-token middleware, yet still fails closed
    on its own hooks-token check. With a UI token configured (middleware active),
    a hooks-bearer request is NOT 401'd by the middleware, but a no-auth request
    IS 401'd by the endpoint."""
    proj = _make_project(tmp_path, "A", token="a.111111")
    cfg = _scaffold(tmp_path, active_project=proj, active_status="WAITING_FOR_HUMAN",
                    ui_token="uitok")
    with patch("ui.server.load_config", return_value=cfg):
        ok = test_client.post("/api/escalation/inbound",
                              json={"text": "retry", "token": "a.111111"}, headers=_AUTH)
        assert ok.status_code != 401  # exempt from UI middleware + hooks auth ok
        noauth = test_client.post("/api/escalation/inbound", json={"text": "retry"})
        assert noauth.status_code == 401  # endpoint fail-closed


def test_inbound_422_when_text_missing(test_client, tmp_path):
    cfg = _scaffold(tmp_path)
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound", json={"sender": "x"}, headers=_AUTH)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Endpoint — token -> project routing (B0 boundedness)
# ---------------------------------------------------------------------------

def test_inbound_token_routes_to_correct_parked_project(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    b = _make_project(tmp_path, "B", token="b.222222")
    c = _make_project(tmp_path, "C")  # active, running, unrelated
    cfg = _scaffold(tmp_path, active_project=c, active_status="RUNNING",
                    queue_entries=[_entry(a, "ESCALATION", position=0),
                                   _entry(b, "ESCALATION", position=1),
                                   _entry(c, "ACTIVE", position=2)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "retry", "token": "b.222222"}, headers=_AUTH)
    assert r.status_code == 200 and r.json()["deferred"] is True
    # answer landed in B (the escalated project), not A or C
    assert (Path(_art(b)) / "pending_escalation_command.json").exists()
    assert not (Path(_art(a)) / "pending_escalation_command.json").exists()
    bdata = json.loads((Path(_art(b)) / "pending_escalation_command.json").read_text())
    assert bdata["command"] == "RETRY" and bdata["source"] == "inbound"


def test_inbound_no_token_single_parked_fallback(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    c = _make_project(tmp_path, "C")
    cfg = _scaffold(tmp_path, active_project=c, active_status="RUNNING",
                    queue_entries=[_entry(a, "ESCALATION", position=0),
                                   _entry(c, "ACTIVE", position=1)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound", json={"text": "proceed"}, headers=_AUTH)
    assert r.status_code == 200
    assert (Path(_art(a)) / "pending_escalation_command.json").exists()


def test_inbound_no_token_two_parked_is_ambiguous_409(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    b = _make_project(tmp_path, "B", token="b.222222")
    cfg = _scaffold(tmp_path, active_status="RUNNING",
                    queue_entries=[_entry(a, "ESCALATION", position=0),
                                   _entry(b, "ESCALATION", position=1)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound", json={"text": "retry"}, headers=_AUTH)
    assert r.status_code == 409
    assert not (Path(_art(a)) / "pending_escalation_command.json").exists()
    assert not (Path(_art(b)) / "pending_escalation_command.json").exists()


def test_inbound_stale_token_no_match_404(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    cfg = _scaffold(tmp_path, active_status="RUNNING",
                    queue_entries=[_entry(a, "ESCALATION", position=0)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "retry", "token": "x.999999"}, headers=_AUTH)
    assert r.status_code == 404
    assert not (Path(_art(a)) / "pending_escalation_command.json").exists()


def test_inbound_resolved_but_not_answerable_409(test_client, tmp_path):
    # token matches a COMPLETED project (no longer parked, not active) -> not answerable
    a = _make_project(tmp_path, "A", token="a.111111")
    cfg = _scaffold(tmp_path, active_status="RUNNING",
                    queue_entries=[_entry(a, "COMPLETED", position=0)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "retry", "token": "a.111111"}, headers=_AUTH)
    assert r.status_code == 409
    assert not (Path(_art(a)) / "pending_escalation_command.json").exists()


# ---------------------------------------------------------------------------
# Endpoint — verb handling, write-path, caps
# ---------------------------------------------------------------------------

def test_inbound_active_waiting_uses_escalation_files_source_inbound(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    cfg = _scaffold(tmp_path, active_project=a, active_status="WAITING_FOR_HUMAN",
                    queue_entries=[_entry(a, "ESCALATION", position=0)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "retry", "token": "a.111111"}, headers=_AUTH)
    assert r.status_code == 200 and r.json()["deferred"] is False
    out = Path(_art(a)) / "escalation_output.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["command"] == "RETRY" and data["source"] == "inbound"
    # not the deferred file
    assert not (Path(_art(a)) / "pending_escalation_command.json").exists()


def test_inbound_unrecognized_text_clarifies_and_writes_nothing(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    cfg = _scaffold(tmp_path, active_project=a, active_status="WAITING_FOR_HUMAN",
                    queue_entries=[_entry(a, "ESCALATION", position=0)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "e1.ab12cd what is going on?", "token": "a.111111"},
                             headers=_AUTH)
    assert r.status_code == 200 and r.json()["status"] == "clarify"
    assert not (Path(_art(a)) / "escalation_output.json").exists()
    assert not (Path(_art(a)) / "pending_escalation_command.json").exists()


def test_inbound_respects_reset_cap_409_no_write(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111", escalation_resets=3)
    cfg = _scaffold(tmp_path, active_project=a, active_status="WAITING_FOR_HUMAN",
                    queue_entries=[_entry(a, "ESCALATION", position=0)])
    with patch("ui.server.load_config", return_value=cfg):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "reset phase", "token": "a.111111"}, headers=_AUTH)
    assert r.status_code == 409
    assert not (Path(_art(a)) / "escalation_output.json").exists()


def test_inbound_acknowledges_on_success(test_client, tmp_path):
    a = _make_project(tmp_path, "A", token="a.111111")
    cfg = _scaffold(tmp_path, active_project=a, active_status="WAITING_FOR_HUMAN",
                    queue_entries=[_entry(a, "ESCALATION", position=0)])
    ack = AsyncMock()
    with patch("ui.server.load_config", return_value=cfg), \
            patch("ui.server._post_inbound_ack", ack):
        r = test_client.post("/api/escalation/inbound",
                             json={"text": "retry", "token": "a.111111"}, headers=_AUTH)
    assert r.status_code == 200 and ack.called

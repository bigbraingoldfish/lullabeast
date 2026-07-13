"""set_session_response_usage() — gateway sessions.patch responseUsage pre-seed.

``responseUsage`` is a per-session-entry preference in OpenClaw (no agent- or
config-level default exists), so the pipeline patches each session via the
gateway WS ``sessions.patch`` method before the ``/hooks/agent`` webhook fires.
The patch handler creates the entry when the key does not exist yet, making the
pre-seed race-free.  These tests mirror test_webhook_client_abort.py's mock-WS
pattern over the shared ``_gateway_request_once`` handshake helper.
"""

import json
from unittest.mock import MagicMock, patch

import webhook_client as wc

CONNECT_CHALLENGE = {
    "type": "event",
    "event": "connect.challenge",
    "payload": {"nonce": "abc", "ts": 1},
}
HELLO_OK = {
    "type": "res",
    "id": "1",
    "ok": True,
    "payload": {"type": "hello-ok"},
}
PATCH_OK = {"type": "res", "id": "2", "ok": True, "payload": {"ok": True}}
PATCH_FAIL = {
    "type": "res",
    "id": "2",
    "ok": False,
    "error": {"type": "invalid_request", "message": "invalid responseUsage"},
}
HEALTH_EVENT = {"type": "event", "event": "health", "payload": {"ok": True}}


def _make_ws_mock(responses):
    ws = MagicMock()
    ws.recv.side_effect = [json.dumps(r) for r in responses]
    return ws


class TestSetSessionResponseUsage:
    def test_returns_true_on_ok(self):
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_OK])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            ok = wc.set_session_response_usage(
                "agent:planner:pipeline:phase-1:core-e1:planner-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert ok is True

    def test_sends_sessions_patch_with_response_usage_full(self):
        """The request frame must be sessions.patch carrying the store key and
        responseUsage="full" (the default mode)."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_OK])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.set_session_response_usage(
                "agent:executor:pipeline:phase-2:core-e2:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        frames = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        patch_frames = [f for f in frames if f.get("method") == "sessions.patch"]
        assert len(patch_frames) == 1
        assert patch_frames[0]["params"] == {
            "key": "agent:executor:pipeline:phase-2:core-e2:executor-attempt-1",
            "responseUsage": "full",
        }

    def test_connect_requests_operator_admin_scope(self):
        """sessions.patch is gated on operator.admin (verified live: requesting
        only operator.write returns INVALID_REQUEST "missing scope:
        operator.admin"), so the connect frame must request it."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_OK])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.set_session_response_usage("agent:planner:k", "ws://x", "tok")
        frames = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        connect = [f for f in frames if f.get("method") == "connect"][0]
        assert connect["params"]["scopes"] == ["operator.admin"]

    def test_mode_override(self):
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_OK])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.set_session_response_usage(
                "agent:planner:k", "ws://x", "tok", mode="tokens"
            )
        frames = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        assert frames[-1]["params"]["responseUsage"] == "tokens"

    def test_omits_model_when_not_passed(self):
        """No model -> the entry bakes the agent's configured default; the patch
        must not carry a model key."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_OK])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.set_session_response_usage("agent:planner:k", "ws://x", "tok")
        assert "model" not in json.loads(ws.send.call_args_list[-1].args[0])["params"]

    def test_bakes_model_on_the_creating_patch_when_passed(self):
        """The override must ride this patch: a session's model is fixed at
        creation, so a later webhook model= cannot change the pre-created entry."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_OK])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.set_session_response_usage(
                "agent:executor:k", "ws://x", "tok", model="openrouter/big/strong"
            )
        params = json.loads(ws.send.call_args_list[-1].args[0])["params"]
        assert params["model"] == "openrouter/big/strong"
        assert params["responseUsage"] == "full"

    def test_returns_false_on_rejection(self):
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, PATCH_FAIL])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            ok = wc.set_session_response_usage("agent:planner:k", "ws://x", "tok")
        assert ok is False

    def test_returns_false_on_connection_error(self):
        """Best-effort: connection failure must not raise."""
        ws = MagicMock()
        ws.connect.side_effect = ConnectionError("refused")
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            ok = wc.set_session_response_usage("agent:planner:k", "ws://x", "tok")
        assert ok is False

    def test_skips_interleaved_event_frames(self):
        """Unsolicited gateway events between request and response are skipped,
        same contract as sessions.abort (the REND-E6 class of bug)."""
        ws = _make_ws_mock(
            [HEALTH_EVENT, CONNECT_CHALLENGE, HEALTH_EVENT, HELLO_OK, HEALTH_EVENT, PATCH_OK]
        )
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            ok = wc.set_session_response_usage("agent:planner:k", "ws://x", "tok")
        assert ok is True

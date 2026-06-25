"""abort_agent_session() — unit tests for the best-effort OpenClaw session abort."""

import json
import websocket as _ws_lib
from unittest.mock import MagicMock, call, patch

import webhook_client as wc


def _make_ws_mock(responses):
    """Return a mock WebSocket that yields JSON responses in sequence from recv()."""
    ws = MagicMock()
    ws.recv.side_effect = [json.dumps(r) for r in responses]
    return ws


# The OpenClaw gateway opens every WebSocket with an unsolicited
# ``connect.challenge`` event carrying a nonce.  The client must wait for
# this frame before sending the ``connect`` request — sending early
# causes the gateway to drop the frame and the handshake to time out.
# This was the live-gateway bug that made every abort_agent_session call
# return False under the original (single-roundtrip) implementation.
CONNECT_CHALLENGE = {
    "type": "event",
    "event": "connect.challenge",
    "payload": {
        "nonce": "abc-test-nonce-1234",
        "ts": 1779255357500,
    },
}

HELLO_OK = {
    "type": "res",
    "id": "1",
    "ok": True,
    "payload": {
        "type": "hello-ok",
        "protocol": 4,
        "server": {},
        "features": {},
        "snapshot": {},
        "auth": {"role": "operator", "scopes": []},
        "policy": {
            "maxPayload": 26214400,
            "maxBufferedBytes": 52428800,
            "tickIntervalMs": 15000,
        },
    },
}
ABORT_ABORTED = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"ok": True, "abortedRunId": "r1", "status": "aborted"},
}
ABORT_NO_RUN = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"ok": True, "abortedRunId": None, "status": "no-active-run"},
}
ABORT_FAIL = {
    "type": "res",
    "id": "2",
    "ok": False,
    "error": {"type": "forbidden", "message": "missing scope"},
}

# ``sessions.steer {key, message:<non-empty>}`` is the embedded-run interrupt the abort
# now issues (``sessions.abort`` cannot see ``/hooks``-launched embedded runs — verified
# live on OpenClaw 2026.6.10: abort against a streaming embedded run returns
# ``no-active-run``). The message MUST be non-empty (the gateway rejects an empty-message
# steer). Success is keyed on the top-level ``ok`` only: an *active* run aborts
# (``interruptedActiveRun:true``) and an already-finished run is a legitimate no-op
# (``aborted:false, runIds:[]``) — both ``ok:true``. These payloads mirror the real
# 2026.6.10 gateway responses.
STEER_INTERRUPTED = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"runId": "r-new", "status": "started", "interruptedActiveRun": True},
}
STEER_ACCEPTED_NO_RUN = {
    "type": "res",
    "id": "2",
    "ok": True,
    "payload": {"ok": True, "aborted": False, "runIds": []},
}


class TestAbortAgentSession:
    def test_returns_true_on_aborted(self):
        """Happy path: session was running and got aborted."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is True

    def test_connect_passes_authorization_header(self):
        """ws.connect must include Authorization: Bearer in HTTP upgrade headers
        AND pass ``suppress_origin=True`` so Python's websocket-client lib does
        not auto-add an ``Origin`` header.

        Suppressing ``Origin`` is load-bearing: with it present, the OpenClaw
        gateway treats the connection as a browser request and rejects the
        trusted-loopback-backend path that grants ``operator.write`` to local
        clients.  Tracked in the live-fire diagnosis on the
        ``can-you-look-into-jiggly-hejlsberg`` plan.
        """
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "my-gateway-token",
            )
        ws.connect.assert_called_once_with(
            "ws://127.0.0.1:18789/__openclaw__/ws",
            header={"Authorization": "Bearer my-gateway-token"},
            suppress_origin=True,
        )

    def test_returns_false_on_401_unauthorized(self):
        """Best-effort: 401 on WebSocket upgrade must not raise, must return False."""
        ws = MagicMock()
        # The message must contain %s placeholders for status_code and status_message.
        ws.connect.side_effect = _ws_lib.WebSocketBadStatusException(
            "Handshake status %s %s", 401, "Unauthorized", {}
        )
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "wrong-token",
            )
        assert result is False

    def test_returns_true_on_no_active_run(self):
        """Session already finished — gateway returns no-active-run, still success."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_NO_RUN])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is True

    def test_returns_false_on_connection_error(self):
        """Best-effort: connection failure must not raise, must return False."""
        ws = MagicMock()
        ws.connect.side_effect = ConnectionError("refused")
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is False

    def test_returns_false_on_timeout(self):
        """Best-effort: socket timeout on second recv must not raise, must return False."""
        import socket

        ws = MagicMock()
        ws.recv.side_effect = [
            json.dumps(CONNECT_CHALLENGE),
            json.dumps(HELLO_OK),
            socket.timeout("timed out"),
        ]
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "test-gateway-token",
            )
        assert result is False

    def test_returns_false_on_auth_rejection(self):
        """Best-effort: gateway scope error must not raise, must return False."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, ABORT_FAIL])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "bad-token",
            )
        assert result is False

    def test_waits_for_connect_challenge_before_sending_connect(self):
        """The OpenClaw gateway opens every WebSocket with an unsolicited
        ``connect.challenge`` event.  The client must receive that frame
        **before** sending ``connect`` — otherwise the gateway treats the
        connect frame as arriving in the wrong state and the handshake
        never completes (every abort returns False).

        Regression test for the bug that made every live abort fail:
        attempt-1 sent ``connect`` immediately, then read the first
        frame and saw the challenge event (which has ``ok`` undefined),
        and treated that as a handshake rejection.

        We assert ordering by checking ``ws.send`` was first called only
        after ``ws.recv`` returned the challenge frame.
        """
        ws = MagicMock()
        ws.recv.side_effect = [
            json.dumps(CONNECT_CHALLENGE),
            json.dumps(HELLO_OK),
            json.dumps(ABORT_ABORTED),
        ]
        call_log = []
        ws.send.side_effect = lambda frame: call_log.append(("send", frame))
        ws.recv.side_effect = (
            lambda: call_log.append(("recv",))
            or [
                json.dumps(CONNECT_CHALLENGE),
                json.dumps(HELLO_OK),
                json.dumps(ABORT_ABORTED),
            ][len([c for c in call_log if c[0] == "recv"]) - 1]
        )
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True
        # First action against the socket must be a recv (to read the
        # challenge), not a send.  This is the protocol invariant the
        # OpenClaw gateway enforces.
        kinds = [c[0] for c in call_log]
        assert kinds[0] == "recv", (
            f"abort_agent_session must read the connect.challenge frame "
            f"before sending the connect request; actual order: {kinds}"
        )

    def test_treats_challenge_event_as_event_not_rejection(self):
        """A bare ``connect.challenge`` event (no ``ok`` key) must not be
        treated as a failed ``hello-ok`` response.  The original bug
        logged ``[ABORT] Gateway handshake rejected`` and returned False
        when the gateway was actually sending a normal challenge frame.
        """
        ws = _make_ws_mock([
            CONNECT_CHALLENGE,
            HELLO_OK,
            ABORT_ABORTED,
        ])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-1:core-e1:executor-attempt-1",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True, (
            "connect.challenge is the normal first frame from the gateway; "
            "abort_agent_session must consume it and proceed, not treat it "
            "as a handshake rejection"
        )

    def test_returns_false_when_res_frame_arrives_before_challenge(self):
        """A ``res`` frame before the challenge is a real protocol violation
        (the client has not sent any request yet, so there is nothing a
        response could answer) — return False rather than blindly proceeding.

        Unsolicited *event* frames, by contrast, are normal gateway chatter
        and are skipped — see TestAbortInterleavedEventFrames.
        """
        weird_first_frame = {
            "type": "res",
            "id": "99",
            "ok": True,
            "payload": {},
        }
        ws = _make_ws_mock([weird_first_frame])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is False


# The gateway pushes unsolicited event frames (health, presence, …) on the
# same socket that carries request/response traffic.  Each wait below must
# skip those and return only the frame it is actually waiting for.  Observed
# live (Minecraft REND-E6, 2026-06-11 02:42): a ``health`` event arrived
# between the ``sessions.abort`` request and its response, the client read
# it as the response, logged "unexpected response", and reported FAILED —
# every abort in the run failed this way, so prior attempts kept streaming
# (zombie sessions) under each retry.
HEALTH_EVENT = {
    "type": "event",
    "event": "health",
    "payload": {"ok": True, "ts": 1781145755747},
}


class TestAbortInterleavedEventFrames:
    def test_succeeds_with_health_event_before_abort_response(self):
        """The exact live failure: a ``health`` event lands between the
        ``sessions.abort`` request and its ``res`` frame.  The client must
        skip the event and read the real response.  Regresses to "every
        abort reports FAILED while the gateway actually aborted the run"."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, HEALTH_EVENT, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-22:rend-e6:executor-attempt-2",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True, (
            "an unsolicited event frame between request and response must be "
            "skipped, not read as the sessions.abort response"
        )

    def test_succeeds_with_events_interleaved_at_every_wait(self):
        """Events may interleave at any of the three wait points (before the
        challenge, before hello-ok, before the abort response).  All three
        must skip them.  Catches a fix applied to only one recv site."""
        ws = _make_ws_mock([
            HEALTH_EVENT,
            CONNECT_CHALLENGE,
            HEALTH_EVENT,
            HELLO_OK,
            HEALTH_EVENT,
            HEALTH_EVENT,
            ABORT_ABORTED,
        ])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "agent:executor:pipeline:phase-22:rend-e6:executor-attempt-2",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True

    def test_event_flood_bounded_by_deadline(self):
        """A gateway that streams events faster than the recv timeout would
        otherwise keep the skip-loop alive forever (each recv returns an
        event before the socket timeout can fire).  The wait must enforce a
        wall-clock deadline derived from ``timeout_seconds`` and give up.

        ``time.monotonic`` is patched to advance 3 s per call so the 8 s
        deadline expires after a few skipped frames; ``time.sleep`` is
        patched out so the 3-attempt retry loop does not really wait."""
        ws = MagicMock()
        ws.recv.return_value = json.dumps(HEALTH_EVENT)  # endless event stream
        fake_clock = {"now": 0.0}

        def _tick():
            fake_clock["now"] += 3.0
            return fake_clock["now"]

        with patch.object(wc.websocket, "WebSocket", return_value=ws), \
             patch.object(wc.time, "monotonic", side_effect=_tick), \
             patch.object(wc.time, "sleep"):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
                timeout_seconds=8,
            )
        assert result is False
        # Deadline must bound the number of frames consumed per attempt to a
        # small multiple of (timeout / tick), not an unbounded stream.
        assert ws.recv.call_count < 20, (
            f"event flood consumed {ws.recv.call_count} frames — the "
            "wall-clock deadline is not bounding the skip loop"
        )

    def test_skips_unknown_event_before_challenge_and_proceeds(self):
        """An unrecognised *event* name before the challenge is gateway
        chatter, not a protocol violation — it must be skipped (the old
        behavior failed the whole abort on any non-challenge first frame)."""
        unknown_event = {
            "type": "event",
            "event": "something.unexpected",
            "payload": {},
        }
        ws = _make_ws_mock([unknown_event, CONNECT_CHALLENGE, HELLO_OK, ABORT_ABORTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key",
                "ws://127.0.0.1:18789/__openclaw__/ws",
                "tok",
            )
        assert result is True


class TestSteerAbort:
    """``abort_agent_session`` must issue ``sessions.steer {key, message:""}`` — the
    only abort that reaches a ``/hooks``-launched embedded run — and treat a
    gateway-accepted (top-level ``ok:true``) response as success, regardless of
    payload ``status``/``interruptedActiveRun`` (an empty-message steer of an
    already-finished run carries neither)."""

    def test_steer_method_and_params(self):
        """The request frame (id "2") must be ``sessions.steer`` with
        ``params={key, message:<non-empty>}`` (the gateway rejects an empty-message
        steer), and an ``ok:true`` response must count as success. Catches a parser
        still keyed on ``payload.status`` (→ every steer returns False = the original
        zombie bug), a method/params left at ``sessions.abort``/``{key}``, or an empty
        message (→ INVALID_REQUEST 'message or attachment required')."""
        sent = []
        ws = MagicMock()
        ws.send.side_effect = lambda frame: sent.append(frame)
        ws.recv.side_effect = [
            json.dumps(CONNECT_CHALLENGE),
            json.dumps(HELLO_OK),
            json.dumps(STEER_INTERRUPTED),
        ]
        key = "agent:reviewer:pipeline:phase-1:core-e1:reviewer-attempt-1"
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                key, "ws://127.0.0.1:18789/__openclaw__/ws", "tok"
            )
        assert result is True
        req2 = [json.loads(f) for f in sent if json.loads(f).get("id") == "2"]
        assert req2, "the abort must send a request frame with id '2'"
        assert req2[0]["method"] == "sessions.steer", (
            f"abort must use sessions.steer (embedded-run interrupt), got "
            f"{req2[0]['method']!r}"
        )
        assert req2[0]["params"]["key"] == key
        assert req2[0]["params"].get("message"), (
            "steer requires a NON-EMPTY message — the gateway rejects an empty-message "
            "steer with INVALID_REQUEST 'message or attachment required'"
        )

    def test_steer_success_on_accepted_no_active_run(self):
        """A steer of an already-finished run returns ``ok:true`` with
        ``aborted:false, runIds:[]`` (a legitimate no-op — the common reviewer-retry
        case). That must be success, so the abort path does not burn its 3× retry on
        an idle session."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, STEER_ACCEPTED_NO_RUN])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            result = wc.abort_agent_session(
                "any:key", "ws://127.0.0.1:18789/__openclaw__/ws", "tok"
            )
        assert result is True

    def test_steer_default_timeout_is_20s(self):
        """The default WS timeout must be 20s, not 8s: the gateway blocks up to
        15s on ``waitForEmbeddedAgentRunEnd`` confirming the run ended, so an 8s
        socket timeout would time out *before* the confirm and report a false
        FAILED."""
        ws = _make_ws_mock([CONNECT_CHALLENGE, HELLO_OK, STEER_INTERRUPTED])
        with patch.object(wc.websocket, "WebSocket", return_value=ws):
            wc.abort_agent_session(
                "any:key", "ws://127.0.0.1:18789/__openclaw__/ws", "tok"
            )
        ws.settimeout.assert_called_with(20)

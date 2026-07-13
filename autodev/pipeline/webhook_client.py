"""OpenClaw gateway client — agent invocation, session abort/verify, usage seeding.

The HTTP + WebSocket layer between the orchestrator and the OpenClaw gateway:

* ``invoke_agent_webhook`` — POST /hooks/agent with a per-call idempotency key and
  the retry/backoff resilience contract; classifies the result as
  ``SUCCESS`` / ``AUTH_ERROR`` / ``REQUEST_ERROR`` / ``INFRA_ERROR`` so the caller
  can route any non-success outcome to escalation.
* ``abort_agent_session`` / ``verify_session_stopped`` — the WebSocket
  ``sessions.steer`` interrupt (retried; the only call that reaches a ``/hooks``
  embedded run) plus the post-interrupt stamp-settle check that confirms a
  prior/zombie attempt has stopped before the next one launches.
* ``set_session_response_usage`` — seeds full response-usage on a session so token
  accounting is captured.

All gateway calls are timeout-bounded; this module performs no pipeline state I/O.
"""
import json
import logging
import os
import time
import uuid

import requests
import websocket


def verify_session_stopped(stamp_path: str, settle_seconds: float = 5.0) -> bool:
    """Confirm a just-interrupted agent session is no longer touching its activity stamp.

    ``sessions.steer`` blocks server-side up to ~15s confirming the embedded run
    ended, but it then returns regardless — if the agent takes longer to wind down,
    the gateway has given up its wait while the process still streams (observed live
    during CORE-E6: attempt #2 wrote 69,152 tokens after attempt #3 had already been
    launched).  This helper is the client-side *stopped* oracle: callers use it
    *after* a successful steer to distinguish "session truly stopped" from "interrupt
    accepted but session still active", so we can emit ``abort_verify_failed`` rather
    than silently launch the next attempt on top of a running one.

    Implementation: read the stamp's mtime, sleep ``settle_seconds``, read
    again.  If mtime did not advance, the plugin is no longer touching it
    and the session is genuinely stopped.

    Parameters
    ----------
    stamp_path:
        Absolute path to the agent's ``{agent}_activity.stamp`` file.
    settle_seconds:
        How long to observe before deciding.  Must be longer than the
        plugin's stamp-refresh cadence (typically every model_call or
        tool_call event, so a few hundred ms during active inference).
        5 s is the default — long enough to be confident, short enough
        to not noticeably delay retry start.

    Returns
    -------
    bool
        ``True`` if the stamp mtime did not advance (or the stamp does not
        exist — a missing stamp cannot be 'active').
        ``False`` if the stamp mtime advanced during the settle window —
        the session is still streaming despite the abort acknowledgement.
    """
    try:
        before = os.path.getmtime(stamp_path)
    except OSError:
        # Missing stamp cannot be active.  Treat as stopped so callers do
        # not get a false "still active" signal that would block retries.
        return True
    time.sleep(settle_seconds)
    try:
        after = os.path.getmtime(stamp_path)
    except OSError:
        return True
    return after == before


# Workspace-relative prefix agents must use for pipeline artifacts (matches PROJECT_ARTIFACTS_DIR /
# .autodev/pipeline on the resolved pipeline-project symlink target).
_PIPELINE_ARTIFACTS = "pipeline-project/.autodev/pipeline"

# OpenClaw POST /hooks/agent accepts optional "thinking" (docs.openclaw.ai/webhook). MiniMax on
# OpenClaw's Anthropic-compatible path defaults to thinking disabled unless set here or in config.
_PIPELINE_WEBHOOK_AGENTS_THINKING = frozenset({"planner", "executor", "reviewer"})
DEFAULT_PIPELINE_THINKING_LEVEL = "medium"


def invoke_agent_webhook(
    agent_id: str,
    session_key: str,
    token: str,
    wake_mode: str = "now",
    model: str = None,
    message: str = None,
    thinking: str | None = None,
    url: str | None = None,
):
    # Enqueue-only semantics: OpenClaw returns HTTP 200 when the agent task has been
    # queued, NOT when it has been executed.  Any 2xx after raise_for_status() means
    # "successfully enqueued" — not "successfully completed".  Do not inspect the
    # response body for execution results here; completion is detected via sentinel
    # files written by the agent to its workspace directory.
    #
    # ``url`` defaults to the loopback gateway but callers pass the orchestrator's
    # resolved ``config["hooks_url"]`` (derived from ``gateway.port``) so a non-default
    # port — and the IPv4 ``127.0.0.1`` over a dual-stack ``localhost`` (which can
    # resolve to ``::1`` where the gateway binds IPv4) — actually take effect.
    url = url or "http://localhost:18789/hooks/agent"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Default messages per agent role — agents read workspace files for full context.
    # Stage D (P0 §2.3): planner / executor / reviewer also read pipeline-project/prd.md
    # and pipeline-project/verification.md. The PRD is the source of truth for user
    # intent; verification.md tells the reviewer how the artifact will be exercised.
    # Escalation is intentionally unchanged — its inputs are phase_state.json + the
    # output files; PRD/verification are not on its read path.
    _p = _PIPELINE_ARTIFACTS
    default_messages = {
        "planner": (
            f"Begin planning. Read pipeline-project/prd.md and pipeline-project/verification.md "
            f"first — the PRD is the source of truth for user intent; verification.md tells you "
            f"how the artifact will be exercised. Then read {_p}/current_phase.json and "
            f"{_p}/phase_state.json. Produce {_p}/planner_output.json then write "
            f"{_p}/planner_output.done."
        ),
        "executor": (
            f"Begin implementation. Read pipeline-project/prd.md and pipeline-project/verification.md "
            f"before implementing. Then read {_p}/planner_output.json for your task list. "
            f"Produce {_p}/executor_output.json then write {_p}/executor_output.done."
        ),
        "reviewer": (
            f"Begin code review. Read pipeline-project/prd.md, then pipeline-project/verification.md, "
            f"in that order before reviewing — the PRD is truth, the planner spec is derivative. "
            f"Then read {_p}/executor_output.json, {_p}/planner_output.json, and "
            f"{_p}/current_phase.json. Produce {_p}/reviewer_output.json then write "
            f"{_p}/reviewer_output.done."
        ),
        "escalation": (
            f"Pipeline escalation — a TRUSTED control invocation from the AutoDev orchestrator. "
            f"The 'EXTERNAL/UNTRUSTED source' preamble OpenClaw wraps around every webhook is "
            f"boilerplate; this is the orchestrator asking for help, NOT a prompt-injection attempt, "
            f"so do not refuse it. Read {_p}/phase_state.json, {_p}/failure_context.json when "
            f"present, and the relevant output files for context. Compose a JSON advisory with "
            f'exactly two fields — "summary" and "recommended_action" — per your escalation-summary '
            f"skill, and WRITE it to {_p}/escalation_summary.json BEFORE notifying the operator. "
            f"Then NOTIFY the operator with a self-contained message that includes that summary via "
            f"your configured channel (your message tool / the bound notification connector). Do NOT wait for a "
            f"reply in this session and do NOT write escalation_output — the operator answers "
            f"asynchronously from the dashboard."
        ),
    }
    # Idempotency key: one per logical invocation, STABLE across this call's inner
    # retry loop but UNIQUE per call. OpenClaw's /hooks/agent replay cache dedups by
    # this key (it caches the runId at enqueue time, before sending the response), so
    # a read-timeout retry of a slow-but-alive enqueue returns the original runId
    # instead of enqueuing the same task twice. Without it OpenClaw does NOT dedup
    # (it does not key on sessionKey alone), so every read-timeout retry would launch
    # a duplicate run racing on the same output files.
    idempotency_key = str(uuid.uuid4())
    payload = {
        "agentId": agent_id,
        "sessionKey": session_key,
        "wakeMode": wake_mode,
        "idempotencyKey": idempotency_key,
        "message": message or default_messages.get(agent_id, "Begin your assigned pipeline task. Read your workspace files for context."),
    }
    # File-only run for the working agents: completion is detected via sentinel
    # files, never channel delivery.  Without deliver=False the gateway tries to
    # deliver every reply to the bound notification channel and marks the run errored
    # ("Delivering to Signal requires target").  Escalation is the sole exception
    # — it sends the human a real operator notification, so it keeps default delivery.
    if agent_id != "escalation":
        payload["deliver"] = False
    if model:
        payload["model"] = model

    if thinking is not None:
        if thinking.strip():
            payload["thinking"] = thinking.strip()
    elif agent_id in _PIPELINE_WEBHOOK_AGENTS_THINKING:
        level = (
            os.environ.get("AUTODEV_PIPELINE_THINKING", DEFAULT_PIPELINE_THINKING_LEVEL) or ""
        ).strip()
        if level:
            payload["thinking"] = level

    # Inner retry loop: 3 attempts, 30s backoff for INFRA failures only
    for attempt in range(1, 4):
        try:
            # (connect, read) split: fail fast on a dead gateway (5 s connect) while
            # still allowing a slow-but-alive enqueue up to 30 s to respond. A read
            # timeout is retried safely because the idempotencyKey above makes the
            # re-POST idempotent at the gateway.
            response = requests.post(url, headers=headers, json=payload, timeout=(5, 30))

            if response.status_code in (401, 403):
                logging.error(f"Auth error (401/403) from webhook: {response.text}")
                return "AUTH_ERROR"

            # Transient infra — RETRYABLE: 429 (rate-limited) and 5xx (server busy /
            # restarting) can self-heal, so keep the 3×30 s retry. This branch MUST
            # precede the deterministic-4xx branch below, because 429 is itself a 4xx.
            if response.status_code == 429 or response.status_code >= 500:
                logging.warning(f"Infra error {response.status_code} (Attempt {attempt}/3): {response.text}")
                if attempt < 3:
                    time.sleep(30)
                    continue
                else:
                    logging.error("Exhausted webhook infra retries.")
                    return "INFRA_ERROR"

            # Deterministic client error (400/404/422/…) — NON-retryable: re-sending
            # the identical request returns the identical rejection (renamed agentId,
            # bad payload shape, malformed body), so fail fast with an honest
            # classification instead of burning 3×30 s and mislabeling it as infra.
            if 400 <= response.status_code < 500:
                logging.error(f"Request error {response.status_code} from webhook: {response.text}")
                return "REQUEST_ERROR"

            response.raise_for_status()
            logging.info(f"Webhook invoked successfully for {agent_id}.")
            return "SUCCESS"
            
        except requests.exceptions.RequestException as e:
            logging.warning(f"Webhook POST failed (Attempt {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(30)
            else:
                logging.error("Exhausted webhook infra retries.")
                return "INFRA_ERROR"
    return "INFRA_ERROR"


ABORT_MAX_ATTEMPTS = 3
ABORT_RETRY_BACKOFF_SECONDS = 2.0


def _recv_expected_frame(ws, session_key, deadline, expect, label):
    """Read frames until the expected one arrives, skipping unsolicited events.

    The gateway multiplexes unsolicited ``event`` frames (``health``,
    presence, …) onto the same socket that carries request/response traffic,
    so a wait for a specific frame must filter — a bare ``ws.recv()`` here
    reads whatever happens to arrive next.  Observed live (REND-E6,
    2026-06-11): a ``health`` event landed between the ``sessions.abort``
    request and its response, was read as the response, and every abort in
    the run reported FAILED while prior attempts kept streaming.

    ``expect`` maps frame keys to required values — ``{"type": "event",
    "event": "connect.challenge"}`` or ``{"type": "res", "id": "2"}`` (we
    send one request at a time, so matching on the request id is exact).
    Non-matching ``event`` frames are skipped; a non-matching ``res`` frame
    is a protocol violation (nothing else was requested) and fails the
    attempt.  ``deadline`` (``time.monotonic()`` basis) bounds the skip loop
    in wall-clock terms: the per-recv socket timeout alone cannot, because a
    gateway emitting events faster than that timeout resets it on every
    frame.  Returns the parsed frame, or ``None`` on violation or deadline
    expiry (socket timeouts raise and are handled by the caller).
    """
    while time.monotonic() < deadline:
        frame = json.loads(ws.recv())
        if all(frame.get(k) == v for k, v in expect.items()):
            return frame
        if frame.get("type") == "event":
            logging.debug(
                "[ABORT] skipping interleaved %r event while waiting for %s (%s)",
                frame.get("event"), label, session_key,
            )
            continue
        logging.warning(
            "[ABORT] unexpected frame (wanted %s) for %s: %s",
            label, session_key, frame,
        )
        return None
    logging.warning(
        "[ABORT] deadline expired waiting for %s for %s", label, session_key
    )
    return None


def _gateway_request_once(
    session_key: str,
    gateway_ws_url: str,
    gateway_token: str,
    timeout_seconds: int,
    method: str,
    params: dict,
    scopes: tuple = ("operator.write",),
):
    """Single gateway WS handshake + one ``method`` request round-trip.

    Returns the parsed response frame (a dict) on a completed round-trip,
    or ``None`` on any failure (connect error, handshake rejection, timeout,
    protocol violation).  Callers interpret the frame's ``ok``/``payload``.

    Gateway handshake protocol (mirrors OpenClaw's own GatewayClient at
    ``client-*.js``: ``handleMessage`` / ``sendConnect``):

    1. Open WebSocket with ``Authorization: Bearer <gateway_token>``.
    2. The gateway sends an unsolicited ``event: connect.challenge``
       frame with ``{nonce, ts}`` *before* accepting any request frames.
       The client MUST read this first frame and not send ``connect``
       before it arrives — sending early causes the gateway to drop
       the frame and the handshake times out (the exact bug that
       made every live abort return False on CORE-E6 / PHYS-E1).
    3. After receiving the challenge, send the ``connect`` request.
       For bearer-token auth (no device identity) the nonce does not
       need to be signed; we just need to have observed the challenge.
    4. Receive ``hello-ok`` response → handshake complete.
    5. Send the request frame and read the result.

    Every frame wait goes through ``_recv_expected_frame``: the gateway
    interleaves unsolicited ``event`` frames (health, …) with response
    traffic, and reading one of those as the response fails the abort
    spuriously (the REND-E6 every-abort-FAILED bug).
    """
    try:
        ws = websocket.WebSocket()
        ws.settimeout(timeout_seconds)
        # Bearer-Auth on the HTTP upgrade request — the gateway validates
        # this before promoting the socket to a WebSocket.
        #
        # ``suppress_origin=True`` is load-bearing: Python's ``websocket-client``
        # library auto-derives an ``Origin: http://127.0.0.1:18789`` header,
        # which the gateway interprets as a *browser* request and rejects
        # the trusted-loopback-backend path that grants our scopes (see
        # OpenClaw's ``shouldSkipLocalBackendSelfPairing``).  Without this
        # flag the gateway returns ``scopes: []`` even on local connections
        # with a valid shared-secret token, so ``sessions.abort`` fails
        # with ``missing scope: operator.write``.
        ws.connect(
            gateway_ws_url,
            header={"Authorization": f"Bearer {gateway_token}"},
            suppress_origin=True,
        )

        # One wall-clock budget for all three frame waits of this attempt.
        # The per-recv socket timeout (settimeout above) bounds a *silent*
        # gateway; this deadline bounds a *chatty* one (see _recv_expected_frame).
        deadline = time.monotonic() + timeout_seconds

        # Step 1: wait for the unsolicited connect.challenge event.  The
        # gateway sends it after the socket opens; sending ``connect``
        # before consuming it is the pre-fix bug.  Other unsolicited events
        # may arrive first and are skipped.
        challenge = _recv_expected_frame(
            ws, session_key, deadline,
            expect={"type": "event", "event": "connect.challenge"},
            label="connect.challenge",
        )
        if challenge is None:
            ws.close()
            return None
        nonce = challenge.get("payload", {}).get("nonce")

        # Step 2: now send the connect request.  The schema is strict:
        # ``client.id`` must come from the published GATEWAY_CLIENT_IDS
        # list and ``client.mode`` from GATEWAY_CLIENT_MODES.  We pose
        # as a generic ``gateway-client`` in ``backend`` mode — same
        # values OpenClaw's own client library uses.  The nonce is NOT
        # carried in the ``auth`` block (the schema rejects unknown
        # properties there); merely observing the challenge before
        # sending ``connect`` is what the gateway requires.
        _ = nonce  # observed; not echoed back for bearer auth
        connect_frame = json.dumps(
            {
                "type": "req",
                "id": "1",
                "method": "connect",
                "params": {
                    "minProtocol": 4,
                    "maxProtocol": 4,
                    "client": {
                        "id": "gateway-client",
                        "version": "1.0.0",
                        "platform": "linux",
                        "mode": "backend",
                    },
                    "role": "operator",
                    "scopes": list(scopes),
                    "caps": [],
                    "commands": [],
                    "permissions": {},
                    "auth": {"token": gateway_token},
                    "locale": "en-US",
                    "userAgent": "autodev-pipeline/orchestrator",
                },
            }
        )
        ws.send(connect_frame)
        hello = _recv_expected_frame(
            ws, session_key, deadline,
            expect={"type": "res", "id": "1"},
            label="connect response",
        )
        if hello is None or not (
            hello.get("ok") and hello.get("payload", {}).get("type") == "hello-ok"
        ):
            logging.warning(
                "[GATEWAY] handshake rejected for %s: %s", session_key, hello
            )
            ws.close()
            return None

        request_frame = json.dumps(
            {
                "type": "req",
                "id": "2",
                "method": method,
                "params": params,
            }
        )
        ws.send(request_frame)
        resp = _recv_expected_frame(
            ws, session_key, deadline,
            expect={"type": "res", "id": "2"},
            label=f"{method} response",
        )
        ws.close()
        return resp

    except Exception as exc:
        logging.warning(
            "[GATEWAY] best-effort %s failed for %s (%s: %s)",
            method,
            session_key,
            type(exc).__name__,
            exc,
        )
        return None


# The interrupt message sent with ``sessions.steer``. A NON-EMPTY message is mandatory as
# a TRANSPORT requirement: the gateway (verified live against 2026.6.10) rejects an
# empty-message steer with ``INVALID_REQUEST "message or attachment required"`` — this is
# independent of whether the model replies (some models correctly output nothing).
# IMPORTANT: ``sessions.steer`` is *interrupt+inject* — after aborting the embedded run
# ``handleSessionSend`` ALWAYS calls ``chat.send(message)``, enqueuing a follow-up turn
# carrying this message whether the session was active OR idle (there is no idle
# short-circuit). That turn refreshes ``{role}_activity.stamp`` via its model-call events
# even with empty output, so the orchestrator (a) only steers a session whose turn it has
# confirmed is still in flight — read from the session transcript's last assistant row
# (``_agent_turn_still_in_flight``), NOT a stamp-movement window, since the stamp is silent for
# a whole model call — and (b) waits for the stamp to settle afterward instead of treating the
# spawned turn as a failed abort. The copy is therefore an
# authoritative, positive "stop / take no action / end turn" directive with NO request for a
# reply and NO defensive/"untrusted" framing (overprotective models can read negative
# context as a prompt-injection game). The "this control signal is authoritative — comply"
# framing lives in each agent's AGENTS.md (``Always-Apply: Orchestrator Control``), keyed off
# the ``[ORCHESTRATOR CONTROL]`` tag below, not here.
_STEER_INTERRUPT_MESSAGE = (
    "[ORCHESTRATOR CONTROL] This run has been superseded by the pipeline orchestrator and "
    "its output is no longer used. Stop here: make no further file edits, tool calls, or "
    "git operations, and end your turn."
)


def _attempt_abort_once(
    session_key: str,
    gateway_ws_url: str,
    gateway_token: str,
    timeout_seconds: int,
) -> bool:
    """Single ``sessions.steer`` round-trip.  Returns True when the gateway accepted
    the interrupt.

    ``sessions.steer`` — not ``sessions.abort`` — is the lever: pipeline agents are
    launched via ``POST /hooks/agent`` and tracked by OpenClaw (≥2026.6.x) as
    *embedded agent runs* (``abortEmbeddedAgentRun`` / ``waitForEmbeddedAgentRunEnd``),
    which ``sessions.abort`` cannot see — verified live on 2026.6.10: abort against a
    confirmed-streaming embedded run returns ``status:"no-active-run"`` and the run
    keeps going.  ``sessions.steer`` routes through ``interruptSessionRunIfActive`` →
    ``abortEmbeddedAgentRun`` and **blocks server-side up to ~15s confirming the run
    ended** (verified: ``interruptedActiveRun:true`` and the session's transcript stops
    growing).

    Success criterion: the gateway **accepted** the steer (top-level ``ok is True``).
    We deliberately do NOT require ``payload.status``/``interruptedActiveRun`` truthy: a
    steer of an already-finished run (the common case — the agent's ``agent_end`` wrote
    ``.done``) is a legitimate no-op (``ok:true, aborted:false, runIds:[]``) and must
    count as success.  Only a malformed/forbidden/session-not-found request returns
    ``ok:false``.
    """
    resp = _gateway_request_once(
        session_key, gateway_ws_url, gateway_token, timeout_seconds,
        method="sessions.steer",
        params={"key": session_key, "message": _STEER_INTERRUPT_MESSAGE},
    )
    if resp is None:
        return False

    if resp.get("ok") is True:
        logging.info(
            "[ABORT] sessions.steer accepted for %s: %s",
            session_key, resp.get("payload", {}),
        )
        return True

    logging.warning(
        "[ABORT] sessions.steer unexpected response for %s: %s", session_key, resp
    )
    return False


def set_session_response_usage(
    session_key: str,
    gateway_ws_url: str,
    gateway_token: str,
    mode: str = "full",
    model: str | None = None,
    timeout_seconds: int = 8,
) -> bool:
    """Set the per-session ``responseUsage`` preference via gateway ``sessions.patch``.

    ``responseUsage: "full"`` makes OpenClaw append a token-usage + cost line to
    every reply the session produces.  It is a **per-session-entry** preference in
    ``sessions.json`` (there is no agent- or config-level default), so it must be
    set on each session.  ``sessions.patch`` **creates** the entry (assigning a
    sessionId) when the key does not exist yet, so calling this *before* the
    ``/hooks/agent`` webhook fires pre-seeds the preference race-free — the run
    then reuses the pre-created entry.

    ``model`` (when given) rides the **same** creating patch so the entry is baked
    with the chosen model. A session's model is fixed at creation, so once this
    patch has created the entry a later webhook ``model=`` has no effect — the
    model must be set here or not at all. Unset it (``None``) to let the entry bake
    the agent's configured default.

    ``session_key`` must be the gateway **store key** — i.e. the
    ``agent:{role}:{bare_key}`` lowercase form (same shape ``sessions.abort``
    expects), not the bare ``pipeline:…`` key.

    Best-effort: returns True when the gateway acknowledged the patch, False on
    any failure.  Callers must never block an agent invocation on this.
    """
    _params = {"key": session_key, "responseUsage": mode}
    if model:
        _params["model"] = model
    resp = _gateway_request_once(
        session_key, gateway_ws_url, gateway_token, timeout_seconds,
        method="sessions.patch",
        params=_params,
        # sessions.patch is gated on operator.admin (sessions.abort needs only
        # operator.write) — verified live against the gateway: requesting
        # operator.write alone returns INVALID_REQUEST "missing scope:
        # operator.admin".
        scopes=("operator.admin",),
    )
    if resp is None:
        return False
    if resp.get("ok"):
        logging.info(
            "[USAGE] sessions.patch responseUsage=%s for %s", mode, session_key
        )
        return True
    logging.warning(
        "[USAGE] sessions.patch rejected for %s: %s", session_key, resp
    )
    return False


def abort_agent_session(
    session_key: str,
    gateway_ws_url: str,
    gateway_token: str,
    timeout_seconds: int = 20,
) -> bool:
    """Interrupt the given OpenClaw session via ``sessions.steer`` (see
    :func:`_attempt_abort_once` for why steer, not abort).

    Uses the gateway WebSocket control plane (not the ``/hooks/agent`` HTTP endpoint).
    Best-effort: any failure returns ``False`` and logs a warning; callers must not
    treat ``False`` as a hard failure that blocks retries.

    Timeout
    -------
    Default ``timeout_seconds=20`` (was 8): ``sessions.steer`` blocks server-side up
    to ~15s in ``waitForEmbeddedAgentRunEnd`` confirming the run ended, so an 8s
    socket timeout would expire *before* the gateway confirms and report a false
    FAILED.  20s leaves ~5s of handshake headroom.

    Retry policy
    ------------
    A single WS handshake against a busy gateway is brittle — observed in CORE-E6
    where one transient handshake failure caused the orchestrator to launch attempt
    N+1 on top of the still-streaming attempt N.  This wrapper tries up to
    ``ABORT_MAX_ATTEMPTS`` times with ``ABORT_RETRY_BACKOFF_SECONDS`` between attempts,
    returning as soon as one succeeds.  After the ceiling we return False; the caller
    still treats that as best-effort (does not halt).  Worst case on a fully-failing
    abort is therefore ``ABORT_MAX_ATTEMPTS × timeout_seconds`` (~60s) — acceptable on
    the give-up paths that call this.

    Returns
    -------
    bool
        ``True`` if any of the ``ABORT_MAX_ATTEMPTS`` attempts succeeded;
        ``False`` if all attempts returned False.
    """
    for attempt in range(1, ABORT_MAX_ATTEMPTS + 1):
        if _attempt_abort_once(session_key, gateway_ws_url, gateway_token, timeout_seconds):
            if attempt > 1:
                logging.info(
                    "[ABORT] sessions.steer succeeded for %s on retry %d/%d",
                    session_key, attempt, ABORT_MAX_ATTEMPTS,
                )
            return True
        if attempt < ABORT_MAX_ATTEMPTS:
            time.sleep(ABORT_RETRY_BACKOFF_SECONDS)
    logging.warning(
        "[ABORT] all %d attempts failed for %s — caller will proceed best-effort",
        ABORT_MAX_ATTEMPTS, session_key,
    )
    return False

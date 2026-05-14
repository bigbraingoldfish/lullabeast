import json
import logging
import os
import time

import requests
import websocket


def verify_session_stopped(stamp_path: str, settle_seconds: float = 5.0) -> bool:
    """Confirm a just-aborted agent session is no longer touching its activity stamp.

    The OpenClaw gateway can acknowledge ``sessions.abort`` with ``ok=true``
    yet leave the underlying agent process streaming (observed live during
    CORE-E6: attempt #2 wrote 69,152 tokens after attempt #3 had already
    been launched).  Callers use this helper *after* a successful abort to
    distinguish "session truly stopped" from "abort acknowledged but
    session still active" so we can escalate to ``HALTED_SILENT`` rather
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
):
    # Enqueue-only semantics: OpenClaw returns HTTP 200 when the agent task has been
    # queued, NOT when it has been executed.  Any 2xx after raise_for_status() means
    # "successfully enqueued" — not "successfully completed".  Do not inspect the
    # response body for execution results here; completion is detected via sentinel
    # files written by the agent to its workspace directory.
    url = "http://localhost:18789/hooks/agent"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Default messages per agent role — agents read workspace files for full context
    _p = _PIPELINE_ARTIFACTS
    default_messages = {
        "planner": (
            f"Begin planning. Read {_p}/current_phase.json and {_p}/phase_state.json. "
            f"Produce {_p}/planner_output.json then write {_p}/planner_output.done."
        ),
        "executor": (
            f"Begin implementation. Read {_p}/planner_output.json for your task list. "
            f"Produce {_p}/executor_output.json then write {_p}/executor_output.done."
        ),
        "reviewer": (
            f"Begin code review. Read {_p}/executor_output.json, {_p}/planner_output.json, "
            f"and {_p}/current_phase.json. Produce {_p}/reviewer_output.json "
            f"then write {_p}/reviewer_output.done."
        ),
        "escalation": (
            f"Pipeline needs human intervention. Read {_p}/phase_state.json and relevant output "
            f"files for context. Send a Signal notification, then write your assessment to "
            f"{_p}/escalation_output.json and {_p}/escalation_output.done."
        ),
    }
    payload = {
        "agentId": agent_id,
        "sessionKey": session_key,
        "wakeMode": wake_mode,
        "message": message or default_messages.get(agent_id, "Begin your assigned pipeline task. Read your workspace files for context."),
    }
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
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            
            if response.status_code in (401, 403):
                logging.error(f"Auth error (401/403) from webhook: {response.text}")
                return "AUTH_ERROR"
                
            if response.status_code == 429 or response.status_code >= 500:
                logging.warning(f"Infra error {response.status_code} (Attempt {attempt}/3): {response.text}")
                if attempt < 3:
                    time.sleep(30)
                    continue
                else:
                    logging.error("Exhausted webhook infra retries.")
                    return "INFRA_ERROR"
                    
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


def abort_agent_session(
    session_key: str,
    gateway_ws_url: str,
    gateway_token: str,
    timeout_seconds: int = 8,
) -> bool:
    """Send ``sessions.abort`` to the OpenClaw gateway for the given session key.

    Uses the gateway WebSocket control plane (not the ``/hooks/agent`` HTTP endpoint).
    Best-effort: any failure returns ``False`` and logs a warning; callers must not
    treat ``False`` as a hard failure that blocks retries.

    Parameters
    ----------
    session_key:
        Full OpenClaw session key (lowercase), e.g.
        ``agent:executor:pipeline:phase-1:core-e1:executor-attempt-1``.
    gateway_ws_url:
        WebSocket URL, e.g. ``ws://127.0.0.1:18789/__openclaw__/ws``.
    gateway_token:
        Shared-secret gateway token (``gateway.auth.token`` in ``openclaw.json``).
        Distinct from the hooks Bearer token used by ``invoke_agent_webhook``.
    timeout_seconds:
        Socket timeout in seconds; kept short so a dead gateway does not stall retries.

    Returns
    -------
    bool
        ``True`` if the gateway reported ``aborted`` or ``no-active-run``;
        ``False`` on handshake failure, transport error, or unexpected response.
    """
    try:
        ws = websocket.WebSocket()
        ws.settimeout(timeout_seconds)
        # The gateway validates the token on the HTTP WebSocket upgrade request,
        # not inside the protocol frame.  Pass it as Authorization: Bearer here so
        # the handshake is accepted before any frames are sent.
        ws.connect(gateway_ws_url, header={"Authorization": f"Bearer {gateway_token}"})

        connect_frame = json.dumps(
            {
                "type": "req",
                "id": "1",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 4,
                    "client": {
                        "id": "autodev-pipeline",
                        "version": "1.0.0",
                        "platform": "linux",
                        "mode": "operator",
                    },
                    "role": "operator",
                    "scopes": ["operator.write"],
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
        hello = json.loads(ws.recv())
        if not (hello.get("ok") and hello.get("payload", {}).get("type") == "hello-ok"):
            logging.warning(
                "[ABORT] Gateway handshake rejected for %s: %s", session_key, hello
            )
            ws.close()
            return False

        abort_frame = json.dumps(
            {
                "type": "req",
                "id": "2",
                "method": "sessions.abort",
                "params": {"key": session_key},
            }
        )
        ws.send(abort_frame)
        resp = json.loads(ws.recv())
        ws.close()

        status = resp.get("payload", {}).get("status")
        if resp.get("ok") and status in ("aborted", "no-active-run"):
            logging.info(
                "[ABORT] sessions.abort for %s: status=%s", session_key, status
            )
            return True

        logging.warning(
            "[ABORT] sessions.abort unexpected response for %s: %s", session_key, resp
        )
        return False

    except Exception as exc:
        logging.warning(
            "[ABORT] best-effort abort failed for %s (%s: %s)",
            session_key,
            type(exc).__name__,
            exc,
        )
        return False

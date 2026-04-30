import logging
import os
import time

import requests

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

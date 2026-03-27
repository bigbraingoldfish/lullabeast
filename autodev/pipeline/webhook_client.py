import time
import requests
import logging

def invoke_agent_webhook(agent_id: str, session_key: str, token: str, wake_mode: str = "now", model: str = None, message: str = None):
    url = "http://localhost:18789/hooks/agent"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Default messages per agent role — agents read workspace files for full context
    default_messages = {
        "planner": "Begin planning. Read pipeline-project/current_phase.json and pipeline-project/phase_state.json. Produce pipeline-project/planner_output.json then write pipeline-project/planner_output.done.",
        "executor": "Begin implementation. Read pipeline-project/planner_output.json for your task list. Produce pipeline-project/executor_output.json then write pipeline-project/executor_output.done.",
        "reviewer": "Begin code review. Read pipeline-project/executor_output.json, pipeline-project/planner_output.json, and pipeline-project/current_phase.json. Produce pipeline-project/reviewer_output.json then write pipeline-project/reviewer_output.done.",
        "escalation": "Pipeline needs human intervention. Read pipeline-project/phase_state.json and relevant output files for context. Send a Signal notification, then write your assessment to pipeline-project/escalation_output.json and pipeline-project/escalation_output.done.",
    }
    payload = {
        "agentId": agent_id,
        "sessionKey": session_key,
        "wakeMode": wake_mode,
        "message": message or default_messages.get(agent_id, "Begin your assigned pipeline task. Read your workspace files for context."),
    }
    if model:
        payload["model"] = model
    
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

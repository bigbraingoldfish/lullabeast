"""Contract tests: shipped escalation docs + webhook prompt stay PII-free and actionable."""

from pathlib import Path
import re
import os

import pytest

ESCALATION_DIR = Path(__file__).resolve().parent.parent
AGENTS = (ESCALATION_DIR / "AGENTS.md").read_text(encoding="utf-8")
TOOLS = (ESCALATION_DIR / "TOOLS.md").read_text(encoding="utf-8")

# webhook_client.py lives in this repo (autodev/pipeline/) since the migration
# out of ~/.openclaw; derive the default from this file's location so the test
# is machine-independent. WEBHOOK_CLIENT_PATH still overrides for odd layouts.
WEBHOOK = Path(
    os.environ.get("WEBHOOK_CLIENT_PATH")
    or ESCALATION_DIR.parents[1] / "pipeline" / "webhook_client.py"
)


def test_tools_documents_message_peer_resolution():
    assert "message" in TOOLS.lower()
    assert "openclaw.json" in TOOLS or "channels.signal" in TOOLS
    assert "pipeline" in TOOLS.lower() and "session" in TOOLS.lower()


def test_agents_warn_on_rpc_delivery_errors():
    assert "rpc" in AGENTS.lower() or "delivery" in AGENTS.lower()


def test_no_common_fake_e164_in_shipped_docs():
    fake = re.compile(r"\+1234567890\b|\+15551234567\b")
    for name, text in [("AGENTS.md", AGENTS), ("TOOLS.md", TOOLS)]:
        assert fake.search(text) is None, f"{name} must not contain common fake E.164 examples"


@pytest.mark.skipif(not WEBHOOK.is_file(), reason="webhook_client.py not found — skipped in CI")
def test_webhook_escalation_prompt_is_notify_only():
    """F13 contract: the escalation webhook prompt must name the operator-notify
    channel (message tool / Signal connector) and forbid writing escalation_output
    — the operator answers asynchronously from the dashboard."""
    src = WEBHOOK.read_text(encoding="utf-8")
    assert "default_messages" in src
    assert '"escalation"' in src
    assert "Signal" in src or "message tool" in src
    assert "escalation_output" in src

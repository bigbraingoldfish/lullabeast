"""F13 — the escalation agent's guidance + the orchestrator's escalation webhook messages must:

  (a) frame a pipeline escalation webhook as a TRUSTED control invocation, so the agent does
      not refuse the orchestrator's own webhook as "untrusted"/prompt-injection (the live
      failure mode observed on the SVG Pictionary + `proj` escalations); and
  (b) be NOTIFY-only — the agent must not be told to wait for / relay an operator reply
      in-session, nor to write `escalation_output` (the operator answers from the dashboard;
      the AutoDev server writes the command).

Static-lint assertions over the repo copies. The runtime copies under
``~/.openclaw/workspace-escalation/`` must stay byte-identical (dual-source rule); the parity
test verifies that when the runtime path is present (skipped in CI where it is not).
"""

import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(REPO, "autodev", "agents", "escalation")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestEscalationDocsTrustedAndNotifyOnly:
    def test_identity_frames_webhook_as_trusted(self):
        """IDENTITY.md must tell the agent the webhook is trusted and the UNTRUSTED preamble is
        boilerplate — the direct fix for the agent refusing its own invocation."""
        t = _read("IDENTITY.md")
        low = t.lower()
        assert "TRUSTED" in t, "IDENTITY.md must frame the webhook as TRUSTED"
        assert "untrusted" in low and "boilerplate" in low, (
            "IDENTITY.md must explain the EXTERNAL/UNTRUSTED preamble is boilerplate, not a refusal trigger"
        )

    def test_agents_frames_webhook_as_trusted(self):
        assert "TRUSTED" in _read("AGENTS.md"), "AGENTS.md must frame the webhook as TRUSTED"

    def test_agents_is_notify_only_and_drops_wait_relay_write(self):
        """The broken wait-for-reply / relay-command / write-escalation_output model must be gone,
        replaced by a notify-only deliverable."""
        low = _read("AGENTS.md").lower()
        assert "notification" in low, "AGENTS.md must describe a notification deliverable"
        assert "wait for their resume command" not in low, "stale wait-for-reply model remains"
        assert "relay their command" not in low, "stale relay-command model remains"
        assert "once the human responds with a resume command, write" not in low, (
            "stale 'write escalation_output once the human responds' Output Contract remains"
        )
        assert "the only files you write are" not in low, (
            "stale 'the ONLY files you write are escalation_output' limitation remains"
        )

    def test_soul_is_notify_only(self):
        low = _read("SOUL.md").lower()
        assert "relay their command" not in low, "stale relay-command model remains in SOUL.md"
        assert "notif" in low, "SOUL.md must describe notifying the operator"

    def test_runtime_docs_in_parity_if_present(self):
        """Dual-source rule: the live runtime copies must match the repo copies byte-for-byte.
        Skipped when the runtime workspace is absent (e.g. CI)."""
        rt = os.path.expanduser("~/.openclaw/workspace-escalation")
        if not os.path.isdir(rt):
            pytest.skip("runtime workspace-escalation not present")
        for name in ("IDENTITY.md", "SOUL.md", "AGENTS.md"):
            rt_path = os.path.join(rt, name)
            if not os.path.exists(rt_path):
                pytest.skip(f"runtime {name} not present")
            with open(rt_path) as f:
                runtime_text = f.read()
            assert _read(name) == runtime_text, f"{name}: repo/runtime drift (dual-source rule)"


class TestOrchestratorEscalationWebhookMessages:
    def test_advisory_webhook_msgs_are_trusted_notify_only(self):
        """The orchestrator's escalation webhook message must be trusted + notify-only: no
        'write your assessment to escalation_output' instruction (what the agent refused),
        with the trusted framing + an explicit 'do NOT write escalation_output'. The two
        dispatch sites (repo-init + main) now share ONE builder
        (_build_escalation_webhook_message) so the messages cannot drift — both-sites
        coverage is the builder phrasing plus a >=2 call-site count."""
        src_path = os.path.join(REPO, "autodev", "pipeline", "orchestrator.py")
        with open(src_path) as f:
            src = f.read()
        # Match substrings that are contiguous within a single string literal (adjacent
        # wrapped literals are separated by quotes in the source, so cross-literal phrases
        # cannot be matched statically).
        assert "then write your assessment to" not in src, (
            "stale escalation_output write instruction remains in an orchestrator webhook message"
        )
        assert "TRUSTED control invocation" in src, (
            "the escalation webhook message must frame the invocation as trusted"
        )
        assert "NOTIFY the operator" in src, (
            "the escalation webhook message must instruct the agent to NOTIFY the operator"
        )
        # Count the call prefix — the main dispatch now passes a reply_token (B1),
        # the repo-init dispatch passes none; both still share the one builder.
        assert src.count("self._build_escalation_webhook_message(") >= 2, (
            "both escalation dispatch sites (repo-init + main) must use the shared message builder"
        )

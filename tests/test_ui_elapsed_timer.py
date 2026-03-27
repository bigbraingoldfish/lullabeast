"""Guardrail: ElapsedTimer must not tick during WAITING_FOR_HUMAN (no active agent work)."""


def test_elapsed_timer_hides_for_waiting_for_human():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    start = html.find("function ElapsedTimer(")
    assert start != -1
    end = html.find("function SkillInjected(", start)
    block = html[start:end]
    assert "WAITING_FOR_HUMAN" in block
    assert "return null" in block or "return null;" in block

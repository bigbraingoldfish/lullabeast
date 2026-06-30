"""Drift guard: install.sh deploys ALL roadmap-converter skills.

Agent-owned skills live in the repo at
``autodev/skill-library/roadmap-converter/{skill}/SKILL.md`` and are deployed by
``install.sh`` into ``~/.openclaw/workspace-roadmap-converter/skills/``. The deploy
loop historically listed only three of the four (``format-correction`` was missing,
so it only ever reached the runtime via a manual copy and drifted from source).
This static lint — same spirit as the systemd/launchd parity guards — pins that
every roadmap-converter skill present in the repo is named in install.sh's deploy
loop, so a fourth (or fifth) skill can never silently fail to deploy again.
"""
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_INSTALL = os.path.join(_REPO, "install.sh")
_SKILL_DIR = os.path.join(_REPO, "autodev", "skill-library", "roadmap-converter")


def _repo_skills():
    return sorted(
        d for d in os.listdir(_SKILL_DIR)
        if os.path.isfile(os.path.join(_SKILL_DIR, d, "SKILL.md"))
    )


def test_install_deploy_loop_lists_every_roadmap_converter_skill():
    with open(_INSTALL, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # The converter deploy loop(s) are the `for skill in ...` lines that name the
    # canonical roadmap-generation skill (distinguishes them from discipline-skill loops).
    conv_loops = [
        l for l in lines
        if re.search(r"for\s+skill\s+in", l) and "roadmap-generation" in l
    ]
    assert conv_loops, "no roadmap-converter `for skill in ... roadmap-generation` deploy loop found"
    skills = _repo_skills()
    assert "format-correction" in skills, "fixture: format-correction skill must exist in the repo"
    for loop in conv_loops:
        for skill in skills:
            assert skill in loop, (
                f"roadmap-converter skill '{skill}' is missing from the install.sh "
                f"deploy loop: {loop.strip()}"
            )

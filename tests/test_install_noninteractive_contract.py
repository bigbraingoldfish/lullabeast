"""Static contract lints for install.sh's installer-mode split.

Reads install.sh as text (no execution), same approach as
test_install_sh_portable.py. Full end-to-end installer runs need a real
OpenClaw tree and stay manual; these lints pin the *structure* the modes
depend on:

  * every ``prompt_yn`` call site documents its non-interactive default with
    an adjacent ``# ci-default:`` comment (the per-prompt decision record);
  * ``--owned-openclaw`` and ``--strict`` are parsed and imply
    ``--non-interactive``;
  * the owned-mode guarded blocks contain no prompts at all;
  * owned mode escalates warnings to fatal;
  * the doctor runs as the final gate and fails the install in owned/strict;
  * no fixed ``<path>.tmp`` config writes remain (concurrent-unsafe).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_ROOT / "install.sh"


@pytest.fixture(scope="module")
def text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lines(text) -> list[str]:
    return text.splitlines()


def _prompt_call_lines(lines: list[str]) -> list[int]:
    """0-based indexes of real prompt_yn CALL sites (not the definition or docs)."""
    out = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("prompt_yn()"):
            continue
        if re.search(r'\bprompt_yn "', line):
            out.append(i)
    return out


def test_every_prompt_site_has_ci_default_comment(lines):
    missing = []
    for i in _prompt_call_lines(lines):
        window = lines[max(0, i - 8):i]
        if not any("# ci-default:" in w for w in window):
            missing.append(f"line {i + 1}: {lines[i].strip()}")
    assert not missing, (
        "prompt_yn call sites without an adjacent '# ci-default:' decision "
        "comment:\n" + "\n".join(missing)
    )


def test_prompt_sites_exist(lines):
    # Sanity: the lint above must actually be exercising call sites.
    assert len(_prompt_call_lines(lines)) >= 8


def test_owned_openclaw_flag_parsed(text):
    assert '"--owned-openclaw"' in text
    m = re.search(r'\[ "\$arg" = "--owned-openclaw" \] && \{ OWNED_OPENCLAW=1; NON_INTERACTIVE=1; \}', text)
    assert m, "--owned-openclaw must set OWNED_OPENCLAW=1 and imply --non-interactive"


def test_strict_flag_parsed(text):
    assert '"--strict"' in text
    m = re.search(r'\[ "\$arg" = "--strict" \] && \{ STRICT=1; NON_INTERACTIVE=1; \}', text)
    assert m, "--strict must set STRICT=1 and imply --non-interactive"


def _owned_regions(lines: list[str]) -> list[tuple[str, list[str]]]:
    regions = []
    current: list[str] | None = None
    name = ""
    for line in lines:
        if "# owned-mode-begin" in line:
            current = []
            name = line.split("# owned-mode-begin", 1)[1].strip(": ").strip()
            continue
        if "# owned-mode-end" in line:
            if current is not None:
                regions.append((name, current))
            current = None
            continue
        if current is not None:
            current.append(line)
    return regions


def test_owned_regions_present(lines):
    names = [n for n, _ in _owned_regions(lines)]
    assert len(names) >= 3, f"expected owned-mode regions for steps 6/7/8, got {names}"


def test_owned_regions_contain_no_prompts(lines):
    offenders = []
    for name, region in _owned_regions(lines):
        for line in region:
            if re.search(r'\bprompt_yn\b', line) and not line.lstrip().startswith("#"):
                offenders.append(f"{name}: {line.strip()}")
    assert not offenders, (
        "owned-mode blocks must never prompt (zero-prompt contract):\n"
        + "\n".join(offenders)
    )


def test_owned_mode_escalates_warnings(text):
    m = re.search(r"warn\(\) \{\n(.*?)\n\}", text, re.DOTALL)
    assert m, "warn() definition not found"
    body = m.group(1)
    assert "OWNED_OPENCLAW" in body and "exit 1" in body, (
        "owned mode must treat every warn() as fatal (exit 1)"
    )


def test_doctor_runs_as_final_gate(text):
    assert "-m autodev.installer.doctor" in text, "install.sh must invoke the doctor"
    # The failing-doctor branch must be fatal in owned/strict mode.
    m = re.search(
        r'if \[ "\$OWNED_OPENCLAW" -eq 1 \] \|\| \[ "\$STRICT" -eq 1 \]; then\n\s*fail "Doctor',
        text,
    )
    assert m, "owned/strict modes must exit 1 when the doctor reports failing checks"


def test_no_fixed_tmp_path_config_writes(text):
    assert 'cfg_path + ".tmp"' not in text, (
        "fixed <path>.tmp writes are concurrent-unsafe; use the mkstemp "
        "unique-temp pattern"
    )


def test_usage_line_documents_modes(text):
    usage = text.splitlines()[1:12]
    joined = "\n".join(usage)
    assert "--owned-openclaw" in joined and "--strict" in joined

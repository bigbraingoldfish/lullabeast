#!/usr/bin/env python3
"""CI smoke assertions against a doctor --json report.

Usage: python3 deploy/smoke_assert.py <doctor.json>

Run by .github/workflows/deploy-image.yml after booting the image with
OFFLINE=1 and capturing `python -m autodev.installer.doctor --json` from
inside the container. Asserts:

  1. The report parses as JSON and has the DoctorReport shape.
  2. No check reports status "fail".
  3. Every check that can run keyless in the container is "ok" (the
     REQUIRED_OK set below).
  4. Model-dependent checks report "skipped", never "fail" (the only one is
     webhook_ping, which is --live-only and OFFLINE never passes --live).

Exit 0 on success; exit 1 naming every offending check so the failing doctor
check appears verbatim in the CI failure output. Stdlib only: this runs both
on the CI runner and under the hermetic test suite (tests/
test_deploy_container_files.py).
"""

import json
import sys

# Checks that must be green in an OFFLINE=1 container boot (no provider key,
# no live probes). Includes template_conformance: the
# smoke exec sets OWNED_OPENCLAW=1, and config drift inside the image is
# exactly what this workflow exists to catch.
REQUIRED_OK = (
    "gateway_up",
    "plugin_deployed",
    "agents_registered",
    "hooks_baseline",
    "secret_sync",
    "symlink_consistency",
    "ports",
    "template_conformance",
)

# --live-only checks: OFFLINE mode must leave them "skipped", never "fail".
REQUIRED_SKIPPED = ("webhook_ping",)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: smoke_assert.py <doctor.json>", file=sys.stderr)
        return 1
    try:
        with open(argv[1], encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError) as e:
        print(f"SMOKE FAIL: doctor report unreadable/unparseable: {e}", file=sys.stderr)
        return 1

    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        print("SMOKE FAIL: doctor report has no 'checks' list", file=sys.stderr)
        return 1
    by_id = {}
    for c in checks:
        if not isinstance(c, dict) or not c.get("id") or not c.get("status"):
            print(f"SMOKE FAIL: malformed check entry: {c!r}", file=sys.stderr)
            return 1
        by_id[c["id"]] = c

    problems: list[str] = []
    for c in checks:
        if c["status"] == "fail":
            problems.append(
                f"check '{c['id']}' FAILED: {c.get('detail', '')}"
                f" (fix: {c.get('fix_hint', '')})"
            )
    for cid in REQUIRED_OK:
        c = by_id.get(cid)
        if c is None:
            problems.append(f"required check '{cid}' is missing from the report")
        elif c["status"] != "ok":
            problems.append(
                f"required check '{cid}' is '{c['status']}', expected 'ok':"
                f" {c.get('detail', '')}"
            )
    for cid in REQUIRED_SKIPPED:
        c = by_id.get(cid)
        if c is None:
            problems.append(f"live-only check '{cid}' is missing from the report")
        elif c["status"] != "skipped":
            problems.append(
                f"live-only check '{cid}' is '{c['status']}', expected 'skipped'"
                " in OFFLINE mode"
            )

    if problems:
        print(f"SMOKE FAIL: {len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    counts = {}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    print(f"SMOKE OK: {len(checks)} checks ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

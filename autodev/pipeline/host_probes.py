"""Deterministic, timeout-bounded host-capability probes for Preflight (PREREQ-2).

This module answers a single question for the Preflight/queue layers: *is a named
capability present on this host?* — without ever raising, hanging, or touching the
network. It is the deterministic backend half of the Prerequisite-Readiness read
that lets a missing **required** tool fail fast at Preflight (with guidance) instead
of being discovered mid-pipeline as ``ERR_FILE_MISSING`` (the ``baseball`` incident).

Public surface:

  - ``probe(capability) -> dict`` — the dispatcher. Returns
    ``{"status": <str>, "detail": <str>, "version"?: <str>, "guidance"?: <str>}``
    where ``status`` is one of ``"found"`` / ``"missing"`` / ``"unknown"``.
  - ``binary_on_path(name, guidance=None) -> dict`` — generic single-binary probe,
    used for the project's entry-point binary and any arbitrary declared tool.

Status contract (the classification is intentional — see the roadmap reconciliation):

  - ``found``   — the binary is on PATH and answered ``--version`` (rc 0). ``version``
                  carries the parsed version token.
  - ``missing`` — the binary is **not** on PATH (``FileNotFoundError``). This is the
                  blockable signal PREREQ-3 maps to a Launch-gating row. ``guidance``
                  carries a one-line "what/where to install" note with the standing
                  "we don't install it; verify the source" caution.
  - ``unknown`` — the probe could not conclude: it timed out, the binary exited
                  non-zero/uninterpretably, or an unexpected error occurred. A false
                  negative must never strand legitimate work, so inconclusive is
                  ``unknown``, never a hard failure.

Guarantees:

  - **Never raises.** Every subprocess failure mode is caught and mapped to a result.
  - **Never hangs.** Every ``subprocess.run`` is bounded by ``PREREQ_PROBE_TIMEOUT``
    (default 10s); a wedged probe yields ``unknown`` and cannot block past the timeout.
  - **Never networks.** Probes only run local ``<binary> --version`` commands.

Kept dependency-light on purpose (only ``os`` / ``re`` / ``subprocess``, no
``orchestrator`` import) so both the UI server and the orchestrator can import it.
"""

import os
import re
import subprocess


# Default probe timeout (seconds); overridable via the PREREQ_PROBE_TIMEOUT env var.
_DEFAULT_PROBE_TIMEOUT = "10"

# First dotted-number token in a tool's ``--version`` output, e.g. "2.39.2" from
# "git version 2.39.2", "18.12.0" from "v18.12.0", "3.11.4" from "Python 3.11.4".
_VERSION_TOKEN_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")

# Standing caution appended to every install hint — Lullabeast never installs tools.
_INSTALL_CAUTION = "we don't install it — verify the source yourself"

# capability name -> (candidate binaries tried in order, one-line install guidance).
_BUILTIN_PROBES = {
    "node": (["node"], "Install Node.js 20+ from https://nodejs.org or via nvm (%s)" % _INSTALL_CAUTION),
    "python": (
        ["python3", "python"],
        "Install Python 3 from https://python.org or your OS package manager (%s)" % _INSTALL_CAUTION,
    ),
    "cargo": (["cargo"], "Install Rust/Cargo via rustup — https://rustup.rs (%s)" % _INSTALL_CAUTION),
    "git": (["git"], "Install Git from https://git-scm.com or your OS package manager (%s)" % _INSTALL_CAUTION),
}

# Browser binaries probed for the "browser" capability, in preference order. PATH-based
# detection is inherently unreliable (macOS browsers are .app bundles, snap/flatpak wrap
# differently), so "none found" degrades to ``unknown`` rather than a misleading ``missing``.
_BROWSER_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "firefox",
    "microsoft-edge",
    "msedge",
]


def _probe_timeout_seconds():
    """Resolve the per-probe subprocess timeout from ``PREREQ_PROBE_TIMEOUT``.

    Mirrors the orchestrator's env-int helpers: a missing/blank/non-numeric value
    falls back to the 10s default, and the result is clamped to a 1s minimum.
    Resolved per call so an env change (or a test ``monkeypatch.setenv``) is honored.
    """
    raw = (os.environ.get("PREREQ_PROBE_TIMEOUT") or "").strip()
    try:
        v = int(raw or _DEFAULT_PROBE_TIMEOUT)
    except ValueError:
        v = int(_DEFAULT_PROBE_TIMEOUT)
    return max(1, v)


def _extract_version(stdout, stderr):
    """Return the first dotted version token across stdout+stderr, else the first
    non-empty line. ``""`` when the command produced no output."""
    blob = ("%s\n%s" % (stdout or "", stderr or "")).strip()
    if not blob:
        return ""
    m = _VERSION_TOKEN_RE.search(blob)
    if m:
        return m.group(0)
    return blob.splitlines()[0].strip()


def _result(status, *, version=None, detail="", guidance=None):
    """Build a probe result dict with a consistent shape. ``version`` / ``guidance``
    keys are included only when supplied, so consumers can rely on their absence."""
    out = {"status": status, "detail": detail}
    if version is not None:
        out["version"] = version
    if guidance is not None:
        out["guidance"] = guidance
    return out


def _probe_binaries(candidates, guidance, *, missing_status="missing", missing_detail=None):
    """Probe ``candidates`` in order, returning the first conclusive result.

    For each candidate, runs ``<name> --version`` bounded by the resolved timeout:

      - ``FileNotFoundError`` (binary absent)  → remember and try the next candidate.
      - ``subprocess.TimeoutExpired``          → ``unknown`` (could not conclude in time).
      - any other exception                    → ``unknown`` (never-raises contract).
      - return code 0                          → ``found`` + extracted version.
      - any non-zero / uninterpretable exit    → ``unknown`` (present but not a clean answer).

    If every candidate raised ``FileNotFoundError`` the capability is absent: returns
    ``missing_status`` (``"missing"`` by default; the browser probe passes ``"unknown"``)
    with ``guidance``.

    The ``except`` ordering is load-bearing: ``FileNotFoundError`` ⊂ ``OSError`` ⊂
    ``Exception``, so the absent-binary case must be caught before the broad nets, or it
    would be misreported as ``unknown`` and a missing required tool would never block.
    """
    timeout = _probe_timeout_seconds()
    last_missing = None
    for name in candidates:
        try:
            result = subprocess.run(
                [name, "--version"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            last_missing = "'%s' not found on PATH" % name
            continue
        except subprocess.TimeoutExpired:
            return _result("unknown", detail="'%s --version' timed out after %ds" % (name, timeout))
        except Exception as exc:  # noqa: BLE001 - never-raises: any other failure is inconclusive
            return _result("unknown", detail="'%s' probe failed: %r" % (name, exc))
        if result.returncode == 0:
            version = _extract_version(result.stdout, result.stderr)
            return _result("found", version=version, detail="%s: %s" % (name, version or "present"))
        return _result("unknown", detail="'%s --version' exited %s" % (name, result.returncode))
    detail = missing_detail or last_missing or "not found on PATH"
    return _result(missing_status, detail=detail, guidance=guidance)


def binary_on_path(name, guidance=None):
    """Probe whether a single named binary is on PATH (via ``<name> --version``).

    Absent → ``missing`` + ``guidance`` (a generic install hint when none supplied);
    present → ``found`` + version; inconclusive → ``unknown``. Used for the project's
    entry-point binary and for any declared tool name with no built-in probe.
    """
    guidance = guidance or ("Install '%s' and ensure it is on PATH (%s)" % (name, _INSTALL_CAUTION))
    return _probe_binaries([name], guidance)


def _probe_browser():
    """Probe for any known browser binary on PATH.

    Returns ``found`` (+version) for the first candidate that responds. When none is
    found the result is ``unknown`` — not ``missing`` — because PATH-based browser
    detection is unreliable cross-platform (macOS .app bundles, snap/flatpak wrappers),
    so absence on PATH is not proof the host has no browser (DEC-4: a probe that cannot
    conclude is ``unknown``, never a hard failure).
    """
    return _probe_binaries(
        _BROWSER_CANDIDATES,
        "Install a browser (Chrome/Chromium/Firefox) if your project needs headful/E2E runs (%s)"
        % _INSTALL_CAUTION,
        missing_status="unknown",
        missing_detail=(
            "no known browser binary on PATH (PATH detection misses macOS .app bundles "
            "and some packaged installs)"
        ),
    )


def probe(capability):
    """Probe a named capability and return its status dict (see module docstring).

    Dispatch:
      - empty/blank name        → ``unknown``.
      - ``"browser"``           → multi-candidate browser probe.
      - a built-in capability   → ``node`` / ``python`` / ``cargo`` / ``git``.
      - anything else           → treated as an entry-point binary name and looked up
                                  on PATH via :func:`binary_on_path` (so PREREQ-3 can
                                  call e.g. ``probe("unity6")`` for an arbitrary tool).

    Never raises; always returns within the resolved probe timeout.
    """
    cap = str(capability or "").strip()
    if not cap:
        return _result("unknown", detail="empty capability name")
    if cap == "browser":
        return _probe_browser()
    if cap in _BUILTIN_PROBES:
        candidates, guidance = _BUILTIN_PROBES[cap]
        return _probe_binaries(candidates, guidance)
    return binary_on_path(cap)

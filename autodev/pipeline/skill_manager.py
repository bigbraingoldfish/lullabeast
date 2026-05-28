"""
skill_manager.py — Per-agent, per-phase discipline skill injector for the AutoDev pipeline.

Integrates with OpenClaw's native workspace-level skills tier:
    <workspace>/skills/{name}/SKILL.md   ← auto-loaded by OpenClaw at session start

Design principles:
  • Graceful degradation everywhere: missing config / bad YAML / absent file → no skill, no crash
  • inject_skill() is idempotent: always cleans before placing, never leaves stale skills
  • Logs [SKILL] lines BEFORE returning so the record exists even if the agent crashes
  • ~/<workspace>/skills/ directory is the ONLY place we write; ~/.openclaw/skills/ (OpenClaw
    global tier) is deliberately left untouched to avoid loading all 27 skills simultaneously
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from env_resolvers import resolve_openclaw_root  # noqa: E402

OPENCLAW_ROOT = resolve_openclaw_root()


def _autodev_repo_path() -> str:
    """Resolve repo root at call time so tests can override AUTODEV_REPO_PATH per case."""
    return os.environ.get(
        "AUTODEV_REPO_PATH",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )


class SkillManager:
    """Manages per-phase skill injection into agent workspaces.

    Two layers of skill are written into ``workspace-{role}/skills/`` on every
    invocation:

      1. **Base skills (always)** — one subdirectory per discipline in
         :pyattr:`BASE_DISCIPLINES`.  These encode universal rules
         (integration-wiring's "read the entrypoint before wiring" rule and
         testing-quality's TDD discipline) that apply on every phase regardless
         of the roadmap prefix.  P1 Stage A introduced this layer.
      2. **Phase-prefix skill (conditional)** — a single subdirectory derived
         from ``phase_raw_id`` via ``skill_mapping.yaml`` when a mapping
         exists.  This is the variable, phase-specific discipline.

    OpenClaw's ``loadWorkspaceSkillEntries`` walks the entire ``skills/``
    directory and loads every ``SKILL.md`` it finds, so writing 2–3
    subdirectories per phase results in all of them being available to the
    agent.  Workspace cleanup at the start of every call ensures no skill from
    a prior phase leaks through.

    Token-budget note: the six base SKILL.md files total ~13 KB across the
    three roles (audited 2026-05-27 during P1 Stage A planning; each file is
    well under the 80-line target documented in the post-P0 roadmap).  Future
    contributors should keep base skills tight — they stack on every phase, on
    top of identity docs and the prefix skill.

    Usage (orchestrator):
        skill_manager = SkillManager(OPENCLAW_ROOT)
        # before each agent webhook call:
        skill_manager.inject_skill(self.state.get("current_phase_raw_id", ""),
                                   "planner", self.openclaw_config)
    """

    # Disciplines always injected for every role on every phase.  Hardcoded
    # (not configurable via skill_mapping.yaml) because these are universal
    # pipeline-level guarantees, not project-specific routing.  Order matters
    # only for determinism in log output; injection results don't depend on it.
    BASE_DISCIPLINES = ("integration-wiring", "testing-quality")

    def __init__(self, workspace_dir: str = OPENCLAW_ROOT):
        self._workspace_dir = workspace_dir
        _rp = _autodev_repo_path()
        self._skill_library_dir = os.path.join(_rp, "autodev", "skill-library")
        self._mapping_file = os.path.join(_rp, "autodev", "config", "skill_mapping.yaml")
        self._mapping: dict = self._load_mapping()
        self._write_health_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject_skill(self, phase_raw_id: str, agent_role: str, openclaw_config: dict) -> None:
        """Inject base + phase-prefix discipline skills into the agent's workspace.

        Layered model (P1 Stage A):

          * **Base skills** (always, if not gated by kill switch) — one
            subdirectory per discipline in :pyattr:`BASE_DISCIPLINES`.
          * **Phase-prefix skill** (conditional) — derived from
            ``phase_raw_id`` via ``skill_mapping.yaml``.  Skipped silently
            (one log line) when ``phase_raw_id`` is empty, the subsystem is
            unmapped, or the source SKILL.md is missing — the base skills
            remain present in all of those cases.

        The workspace is cleaned exactly once at the start of the call.  Each
        skill written emits a ``[SKILL] ... Status=loaded base=true|false``
        log line; failures emit a ``Status=none_found`` line and continue so
        that one missing source file cannot strip the rest.

        Called immediately after ``cleanup_output_files()`` and before
        ``invoke_agent_webhook()`` in ``orchestrator.py``.

        Args:
            phase_raw_id:    Current phase identifier, e.g. "CORE-E2".  Empty string is safe.
            agent_role:      One of "planner", "executor", "reviewer".
            openclaw_config: Live openclaw.json dict (re-read each call to support flag toggles).
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        workspace_skills_dir = os.path.join(
            self._workspace_dir, f"workspace-{agent_role}", "skills"
        )

        # ── 1. Read skill toggle flags fresh from config ──────────────────────
        skills_cfg = (
            openclaw_config.get("pipeline", {}).get("skills", {})
        )
        globally_enabled = skills_cfg.get("enabled", True)
        agent_enabled    = skills_cfg.get(f"{agent_role}_skills_enabled", True)

        if not globally_enabled:
            self._clean_workspace_skills(workspace_skills_dir)
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", "Status=disabled Reason=skills_disabled_globally")
            return

        if not agent_enabled:
            self._clean_workspace_skills(workspace_skills_dir)
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", f"Status=disabled Reason={agent_role}_skills_disabled")
            return

        # ── 2. Single cleanup at start of method ──────────────────────────────
        # Both layers (base and prefix) are written after this point, with no
        # further cleanups, so the prefix skill cohabitates with the base
        # skills in a single freshly-prepared directory.
        if not self._clean_workspace_skills(workspace_skills_dir):
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", "Status=clean_failed Reason=rmtree_error")
            return

        # ── 3. Inject base skills (always) ────────────────────────────────────
        # One per discipline in BASE_DISCIPLINES.  A missing source file or
        # copy error on any one of them does NOT abort the rest of the call —
        # base + prefix coverage degrades independently.
        for base_discipline in self.BASE_DISCIPLINES:
            self._copy_skill_into_workspace(
                timestamp, phase_raw_id, agent_role,
                base_discipline, workspace_skills_dir, is_base=True,
            )

        # ── 4. Inject phase-prefix skill (conditional) ────────────────────────
        # Each of the three skip cases (empty ID, unmapped subsystem, missing
        # source) logs one explanatory line and falls through to the end of
        # the method.  Base skills written in step 3 stay in place.
        if not phase_raw_id:
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", "Status=none_mapped Reason=empty_phase_id")
            return

        subsystem = phase_raw_id.split("-")[0].upper()
        discipline = self._mapping.get(subsystem)
        if not discipline:
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", f"Status=none_mapped Reason=no_mapping_for_{subsystem}")
            return

        self._copy_skill_into_workspace(
            timestamp, phase_raw_id, agent_role,
            discipline, workspace_skills_dir, is_base=False,
        )

    def _copy_skill_into_workspace(
        self,
        timestamp: str,
        phase_raw_id: str,
        agent_role: str,
        discipline: str,
        workspace_skills_dir: str,
        is_base: bool,
    ) -> None:
        """Copy one ``{discipline}/{role}/SKILL.md`` into ``workspace-{role}/skills/``.

        Emits a single ``[SKILL]`` log line in every case — ``Status=loaded``
        on success, ``Status=none_found`` on missing source or copy error.
        Never raises: a failure here is recorded and absorbed so the caller's
        loop / fall-through continues.

        Args:
            is_base: True for ``BASE_DISCIPLINES`` entries, False for the
                phase-prefix discipline.  Surfaced in the log line as
                ``base=true|false`` so operators grepping the orchestrator
                log can tell which layer a given line came from.
        """
        source_path = os.path.join(
            self._skill_library_dir, discipline, agent_role, "SKILL.md"
        )
        base_token = "true" if is_base else "false"
        if not os.path.exists(source_path):
            reason = "missing_base_skill" if is_base else "missing_file"
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE",
                      f"Status=none_found Reason={reason} discipline={discipline} "
                      f"base={base_token} path={source_path}")
            return

        skill_name = f"{discipline}-{agent_role}"  # e.g. "core-logic-executor"
        dest_dir = os.path.join(workspace_skills_dir, skill_name)
        dest_path = os.path.join(dest_dir, "SKILL.md")

        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except OSError as exc:
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE",
                      f"Status=none_found Reason=copy_error discipline={discipline} "
                      f"base={base_token} error={exc}")
            return

        self._log(timestamp, phase_raw_id, agent_role,
                  f"{discipline}/{agent_role}/SKILL.md",
                  f"Status=loaded base={base_token}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_mapping(self) -> dict:
        """Load skill_mapping.yaml into a normalised (uppercase-key) dict.

        Returns an empty dict on any failure — graceful degradation.
        """
        if not _YAML_AVAILABLE:
            print("[SKILL] [WARN] PyYAML not available — skills mapping disabled. "
                  "Install pyyaml to enable skill injection.")
            return {}

        if not os.path.exists(self._mapping_file):
            print(f"[SKILL] [WARN] Mapping file not found: {self._mapping_file} "
                  "— skills will not be loaded for any phase.")
            return {}

        try:
            with open(self._mapping_file, "r") as fh:
                raw = yaml.safe_load(fh)
            if not isinstance(raw, dict):
                print(f"[SKILL] [ERROR] Mapping file did not parse to a dict: "
                      f"{self._mapping_file} — skills disabled.")
                return {}
            # Normalise keys to uppercase; strip whitespace from values
            return {k.upper(): str(v).strip() for k, v in raw.items() if k and v}
        except yaml.YAMLError as exc:
            print(f"[SKILL] [ERROR] Bad YAML in mapping file {self._mapping_file}: "
                  f"{exc} — skills disabled.")
            return {}
        except Exception as exc:  # noqa: BLE001
            print(f"[SKILL] [ERROR] Failed to load mapping file {self._mapping_file}: "
                  f"{exc} — skills disabled.")
            return {}

    def _write_health_file(self) -> None:
        """Write skill_health.json to OPENCLAW_ROOT for operator visibility.

        Contains a snapshot of skill-injection readiness at construction time.
        Never raises — health reporting must not crash the pipeline.
        """
        health = {
            "yaml_available": _YAML_AVAILABLE,
            "mapping_loaded": bool(self._mapping),
            "mapping_count": len(self._mapping),
            "mapping_file": self._mapping_file,
            "skill_library_dir": self._skill_library_dir,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        health_path = os.path.join(self._workspace_dir, "skill_health.json")
        try:
            os.makedirs(self._workspace_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._workspace_dir, prefix=".skill_health_")
            try:
                os.write(fd, json.dumps(health, indent=2).encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp, health_path)
        except OSError as exc:
            print(f"[SKILL] [WARN] Could not write skill_health.json: {exc}")

    def _clean_workspace_skills(self, skills_dir: str) -> bool:
        """Remove and recreate the workspace skills/ directory (idempotent).

        Removes the entire dir tree so no stale skill from a prior phase can linger.
        Recreates an empty dir so OpenClaw always sees a valid (possibly empty) skills/.

        Returns True on success, False if the directory could not be cleaned.
        Callers must check the return value and not proceed with skill injection on False —
        injecting into a dirty tree (stale skill still present) is worse than no injection.
        """
        try:
            if os.path.exists(skills_dir):
                shutil.rmtree(skills_dir)
            os.makedirs(skills_dir, exist_ok=True)
            return True
        except OSError as exc:
            print(f"[SKILL] [WARN] Could not clean workspace skills dir "
                  f"{skills_dir}: {exc}")
            return False

    @staticmethod
    def _log(timestamp: str, phase_raw_id: str, agent_role: str,
             skill_label: str, status_str: str) -> None:
        """Emit a grep-friendly [SKILL] log line to stdout."""
        print(
            f"[SKILL] ts={timestamp} Phase={phase_raw_id or 'NONE'} "
            f"Agent={agent_role} Skill={skill_label} {status_str}"
        )

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
import tempfile
from datetime import datetime, timezone

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


AUTODEV_ROOT = os.environ.get("AUTODEV_ROOT", os.path.expanduser("~/.openclaw"))


def _autodev_repo_path() -> str:
    """Resolve repo root at call time so tests can override AUTODEV_REPO_PATH per case."""
    return os.environ.get(
        "AUTODEV_REPO_PATH",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )


class SkillManager:
    """Manages per-phase skill injection into agent workspaces.

    Usage (orchestrator):
        skill_manager = SkillManager(AUTODEV_ROOT)
        # before each agent webhook call:
        skill_manager.inject_skill(self.state.get("current_phase_raw_id", ""),
                                   "planner", self.openclaw_config)
    """

    def __init__(self, workspace_dir: str = AUTODEV_ROOT):
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
        """Inject the appropriate skill into the agent's workspace, or clean if none applies.

        Always logs a [SKILL] line before returning.  Called immediately after
        cleanup_output_files() and before invoke_agent_webhook() in orchestrator.py.

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

        # ── 2. Extract subsystem from phase ID ────────────────────────────────
        if not phase_raw_id:
            self._clean_workspace_skills(workspace_skills_dir)
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", "Status=none_mapped Reason=empty_phase_id")
            return

        subsystem = phase_raw_id.split("-")[0].upper()

        # ── 3. Map subsystem → discipline ─────────────────────────────────────
        discipline = self._mapping.get(subsystem)
        if not discipline:
            self._clean_workspace_skills(workspace_skills_dir)
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", f"Status=none_mapped Reason=no_mapping_for_{subsystem}")
            return

        # ── 4. Locate source SKILL.md in library ──────────────────────────────
        source_path = os.path.join(
            self._skill_library_dir, discipline, agent_role, "SKILL.md"
        )
        if not os.path.exists(source_path):
            self._clean_workspace_skills(workspace_skills_dir)
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE",
                      f"Status=none_found Reason=missing_file path={source_path}")
            return

        # ── 5. Clean then inject ───────────────────────────────────────────────
        if not self._clean_workspace_skills(workspace_skills_dir):
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE", "Status=clean_failed Reason=rmtree_error")
            return

        skill_name = f"{discipline}-{agent_role}"      # e.g. "core-logic-executor"
        dest_dir   = os.path.join(workspace_skills_dir, skill_name)
        dest_path  = os.path.join(dest_dir, "SKILL.md")

        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except OSError as exc:
            # Copy failed — treat as none_found to maintain graceful degradation
            self._log(timestamp, phase_raw_id, agent_role,
                      "NONE",
                      f"Status=none_found Reason=copy_error error={exc}")
            return

        self._log(timestamp, phase_raw_id, agent_role,
                  f"{discipline}/{agent_role}/SKILL.md",
                  "Status=loaded")

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
        """Write skill_health.json to AUTODEV_ROOT for operator visibility.

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

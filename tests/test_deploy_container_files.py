"""Static lints for the container deploy files.

Hermetic by construction: every test reads repo files (or runs `bash -n` /
`git check-ignore` / `python deploy/smoke_assert.py` against tmp fixtures);
nothing touches ~/.openclaw, the live .autodev tree, or the network. Real
`docker build` / `docker compose up` runs are manual acceptance (and CI);
do not fake them here.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from autodev.installer.openclaw_template import TEMPLATE_MODEL_DEFAULTS

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "deploy"

DOCKERFILE = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = (DEPLOY / "entrypoint.sh").read_text(encoding="utf-8")
COMPOSE_PATH = DEPLOY / "docker-compose.yml"
DEV_COMPOSE_PATH = DEPLOY / "docker-compose.dev.yml"
ENV_EXAMPLE = (DEPLOY / ".env.example").read_text(encoding="utf-8")
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-image.yml"
WORKFLOW_TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
SMOKE_ASSERT = DEPLOY / "smoke_assert.py"


class TestDockerfile:
    def test_openclaw_version_pinned_exactly(self):
        # The pin is a settled round-2 decision (2026.6.11) and must never
        # float: "latest" anywhere in the OpenClaw install line is a bug.
        assert re.search(
            r"^ARG OPENCLAW_VERSION=2026\.6\.11$", DOCKERFILE, re.MULTILINE
        ), "Dockerfile must pin ARG OPENCLAW_VERSION=2026.6.11"
        assert "openclaw@latest" not in DOCKERFILE

    def test_base_image_tag_pinned(self):
        m = re.search(r"^FROM\s+(\S+)$", DOCKERFILE, re.MULTILINE)
        assert m, "no FROM line found"
        image = m.group(1)
        assert re.fullmatch(
            r"mcr\.microsoft\.com/playwright:v\d+\.\d+\.\d+-\w+", image
        ), f"base image must be an exactly pinned Playwright tag, got {image}"

    def test_nonroot_user_created_and_used(self):
        assert "useradd" in DOCKERFILE
        assert re.search(r"^USER lullabeast$", DOCKERFILE, re.MULTILINE)

    def test_base_default_users_removed_before_useradd(self):
        # The Playwright base ships both pwuser (uid 1001) and
        # ubuntu (uid 1000); both must be freed before useradd --uid 1000, or
        # the build dies with "UID already in use".
        pwuser_idx = DOCKERFILE.find("userdel -r pwuser")
        ubuntu_idx = DOCKERFILE.find("userdel -r ubuntu")
        useradd_idx = DOCKERFILE.find("useradd --create-home")
        assert pwuser_idx != -1, "must remove pwuser"
        assert ubuntu_idx != -1, "must remove ubuntu"
        assert useradd_idx != -1
        assert pwuser_idx < useradd_idx and ubuntu_idx < useradd_idx, (
            "both default users must be removed before useradd"
        )

    def test_system_safe_directory_configured(self):
        # Docker Desktop bind mounts arrive root-owned; a
        # system-level safe.directory disables the dubious-ownership guard so
        # pipeline git work survives container recreation.
        assert re.search(
            r"git config --system --add safe\.directory '\*'", DOCKERFILE
        ), "Dockerfile must bake a system-level safe.directory git config"

    def test_entrypoint_wired(self):
        assert 'ENTRYPOINT ["/app/deploy/entrypoint.sh"]' in DOCKERFILE

    def test_container_path_contract(self):
        # The layout every other artifact (entrypoint, EVAL-MIGRATION.md,
        # compose volumes) assumes.
        assert "OPENCLAW_ROOT=/data/openclaw" in DOCKERFILE
        assert "AUTODEV_REPO_PATH=/app" in DOCKERFILE
        assert "AUTODEV_PIPELINE_ROOT=/data/pipeline-state" in DOCKERFILE


class TestEntrypoint:
    def test_bash_syntax(self):
        proc = subprocess.run(
            ["bash", "-n", str(DEPLOY / "entrypoint.sh")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

    def test_executable_bit(self):
        assert (DEPLOY / "entrypoint.sh").stat().st_mode & 0o111

    def test_install_sh_is_the_provisioning_brain(self):
        # Cross-cutting risk 3: the entrypoint calls install.sh --owned-openclaw;
        # nothing reimplements its steps.
        assert "--owned-openclaw" in ENTRYPOINT
        assert re.search(r"install\.sh.*--owned-openclaw", ENTRYPOINT)

    def test_template_rendered_via_shared_helper(self):
        # First-boot config render must go through openclaw_template's
        # render_template_text, never a hand-rolled sed.
        assert "render_template_text" in ENTRYPOINT

    def test_config_reconciled_toward_template_on_boot(self):
        # A persisted openclaw.json must be reconciled toward the current
        # image's template on boot (not only rendered when absent), or a
        # template change in an upgrade dead-ends at owned-mode validation and
        # crash-loops the container.
        assert "reconcile_config_to_template" in ENTRYPOINT

    def test_atomic_write_helpers_used(self):
        assert "write_text_atomic" in ENTRYPOINT
        assert "write_json_atomic" in ENTRYPOINT

    def test_live_doctor_ping_marked_once(self):
        # The billable --live webhook ping must be marked spent BEFORE the
        # doctor runs, so a doctor FAIL (die) does not re-fire the paid ping on
        # every restart. Guard against a regression that moves the touch back
        # after the doctor: the marker touch must precede the doctor invocation.
        touch_idx = ENTRYPOINT.find('touch "$FIRST_BOOT_MARKER"')
        doctor_idx = ENTRYPOINT.find("python3 -m autodev.installer.doctor")
        assert touch_idx != -1 and doctor_idx != -1
        assert touch_idx < doctor_idx, (
            "FIRST_BOOT_MARKER must be touched before the doctor runs"
        )

    def test_api_key_contract(self):
        # Missing key exits before starting anything, naming both variables.
        assert "ANTHROPIC_API_KEY" in ENTRYPOINT
        assert "OPENROUTER_API_KEY" in ENTRYPOINT

    def test_prints_dashboard_url_with_token(self):
        assert "AUTODEV_UI_TOKEN" in ENTRYPOINT
        assert re.search(r"http://127\.0\.0\.1:\$\{UI_PORT\}/\?token=", ENTRYPOINT)

    def test_dashboard_url_is_colorized_last(self):
        # The Dashboard URL must be the blazing final output of boot: it is
        # printed in bold-green ANSI (\033[1;32m ... \033[0m) via a printf so it
        # stands out. All three banners route through the shared helper.
        assert re.search(
            r"printf '\\033\[1;32m  Dashboard:  %s\\033\[0m\\n'", ENTRYPOINT
        ), "the Dashboard line must be printed in bold-green ANSI via printf"
        # Each banner function must emit the colorized Dashboard line.
        for fn in ("banner_up", "banner_setup_mode", "banner_unlocked"):
            m = re.search(rf"{fn}\(\)\s*\{{(.*?)\n\}}", ENTRYPOINT, re.DOTALL)
            assert m, f"could not locate {fn} body"
            assert "banner_dashboard_line" in m.group(1), (
                f"{fn} must print the colorized Dashboard line"
            )
        # The CI log-grep needle must stay a plain, un-colorized literal (the
        # deploy workflow greps docker logs for exactly this byte sequence).
        assert 'echo "  Lullabeast is up."' in ENTRYPOINT

    def test_supervised_loops_present(self):
        # Cron jobs become supervised sleep loops in-container; the scripts
        # themselves are unchanged.
        assert "heartbeat_cron.py" in ENTRYPOINT
        assert "session_cleanup.py" in ENTRYPOINT
        assert "sleep 1800" in ENTRYPOINT
        assert "sleep 86400" in ENTRYPOINT

    def test_openclaw_cli_state_dir_mapped(self):
        # The openclaw CLI ignores Lullabeast's OPENCLAW_ROOT; both container
        # levers must be present (OPENCLAW_STATE_DIR env + ~/.openclaw symlink).
        assert "OPENCLAW_STATE_DIR" in ENTRYPOINT
        assert re.search(r"ln -sfn .*\$HOME/\.openclaw", ENTRYPOINT)

    def test_seeds_setup_paths_into_ui_config(self):
        # The UI-port seeding block also seeds the container-only setup keys the
        # server and doctor read (WP-App + provider_key check + local models),
        # plus the apply-request marker path the settings surface will write.
        assert 'cfg["provider_key_path"]' in ENTRYPOINT
        assert 'cfg["setup_marker_path"]' in ENTRYPOINT
        assert 'cfg["projects_dir"]' in ENTRYPOINT
        assert 'cfg["local_model_probe_host"]' in ENTRYPOINT
        assert 'cfg["apply_request_path"]' in ENTRYPOINT
        assert 'cfg["model_overrides_path"]' in ENTRYPOINT

    def test_seeds_ideas_dir_from_openclaw_root(self):
        # The Ideas plugin writes activity stamps and turn sentinels under
        # $OPENCLAW_ROOT/ideas; the server derives ideas_dir from the pipeline
        # root when it is unset, which points it at the wrong tree and leaves a
        # completed PRD turn undetected (the chat hangs "pending"). A fresh
        # container build excludes ui/config.json, so the entrypoint must seed
        # ideas_dir itself rather than rely on a bind mount's explicit value.
        assert 'cfg["ideas_dir"] = os.path.join(os.environ["OPENCLAW_ROOT"], "ideas")' in ENTRYPOINT

    def test_model_overrides_overlay_applied_in_render(self):
        # Stage B: the dashboard-owned overlay must be re-applied after every
        # render/reconcile (reconcile force-wins template values) and before
        # wire_or_probe_local_models (whose merge preserves existing fields).
        assert 'MODEL_OVERRIDES_FILE="$DATA/model-overrides.json"' in ENTRYPOINT
        render_def = ENTRYPOINT.find("render_reconcile_config() {")
        render_end = ENTRYPOINT.find("wire_or_probe_local_models() {")
        render_body = ENTRYPOINT[render_def:render_end]
        assert "load_model_overrides" in render_body
        assert "apply_model_overrides" in render_body

    def test_config_render_reconcile_is_a_function(self):
        # v1.0.0 Phase 3: the render/reconcile heredoc is wrapped in a function
        # so the setup-watch loop can re-run it, and called in the boot path.
        assert "render_reconcile_config() {" in ENTRYPOINT
        # Called at least twice: the boot path and the watch loop.
        assert ENTRYPOINT.count("render_reconcile_config\n") + ENTRYPOINT.count(
            "        render_reconcile_config\n"
        ) >= 2

    def test_local_model_wiring_function_defined_and_called(self):
        # v1.0.0 Phase 3, B4: wire_or_probe_local_models is defined and called
        # right after render_reconcile_config in the boot path.
        assert "wire_or_probe_local_models() {" in ENTRYPOINT
        def_idx = ENTRYPOINT.find("wire_or_probe_local_models() {")
        # The boot-path call comes after the definition.
        boot_call = ENTRYPOINT.find("\nrender_reconcile_config\nwire_or_probe_local_models")
        assert boot_call > def_idx, "boot must call the two wiring functions in order"

    def test_local_model_url_gate_and_probe(self):
        # The function wires LOCAL_MODEL_URL when set (normalize + probe + merge
        # + atomic write) and probes known local servers in setup mode.
        assert "LOCAL_MODEL_URL" in ENTRYPOINT
        assert "normalize_local_base_url" in ENTRYPOINT
        assert "build_local_provider_entry" in ENTRYPOINT
        assert "merge_local_provider" in ENTRYPOINT
        assert "discover_local_servers" in ENTRYPOINT

    def test_both_first_boot_marker_touches_precede_their_doctor(self):
        # The billable --live ping must be marked spent BEFORE the doctor runs
        # at BOTH sites: the first-boot block and the setup-watch unlock. A
        # regression that moves either touch after its doctor re-fires the paid
        # ping on a crash loop. (test_live_doctor_ping_marked_once only checks
        # the first site via .find(); this covers both.)
        touch_idxs = [
            m.start() for m in re.finditer(r'touch "\$FIRST_BOOT_MARKER"', ENTRYPOINT)
        ]
        doctor_idxs = [
            m.start()
            for m in re.finditer(r"python3 -m autodev\.installer\.doctor", ENTRYPOINT)
        ]
        assert len(touch_idxs) == 2, "expected exactly two FIRST_BOOT_MARKER touches"
        # Each touch must be followed by a doctor invocation, and no doctor
        # invocation may sit between a touch and be un-paired: pair them in order.
        for touch in touch_idxs:
            following = [d for d in doctor_idxs if d > touch]
            assert following, f"no doctor invocation follows the touch at {touch}"


class TestCompose:
    def test_parses_and_single_service(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        assert list(data["services"].keys()) == ["lullabeast"]

    def test_both_ports_published_loopback_only(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        ports = data["services"]["lullabeast"]["ports"]
        assert len(ports) == 2
        assert all(str(p).startswith("127.0.0.1:") for p in ports)

    def test_gateway_port_published_loopback(self):
        # The OpenClaw UI owns model/provider management and the one-time
        # gate-script approvals, so 18789 is reachable from the host loopback.
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        ports = data["services"]["lullabeast"]["ports"]
        assert "127.0.0.1:18789:18789" in [str(p) for p in ports]

    def test_volumes_and_bind_mount(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        volumes = data["services"]["lullabeast"]["volumes"]
        assert "lullabeast-data:/data" in volumes
        assert "./projects:/data/projects" in volumes
        assert "lullabeast-data" in data["volumes"]

    def test_host_gateway_bridge(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        extra = data["services"]["lullabeast"]["extra_hosts"]
        assert "host.docker.internal:host-gateway" in extra

    def test_env_file_wired_optional(self):
        # DELIBERATE change (v1.0.0 Phase 3, B2): env_file is the long-form
        # optional shape so `docker compose up` with no .env file boots into
        # setup mode instead of erroring. Needs Docker Compose v2.24+.
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        env_file = data["services"]["lullabeast"]["env_file"]
        assert env_file == [{"path": ".env", "required": False}]
        assert "v2.24" in COMPOSE_PATH.read_text(encoding="utf-8")

    def test_pull_policy_build(self):
        # The default path always builds locally and
        # never pulls from a registry.
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        assert data["services"]["lullabeast"]["pull_policy"] == "build"


class TestDevCompose:
    """docker-compose.dev.yml: the development stack. Same image and boot
    contract as the user stack; the deltas are the writable /app bind mount,
    separate state/ports, and DEV_MODE=1. Hardening must stay at parity."""

    def _svc(self):
        data = yaml.safe_load(DEV_COMPOSE_PATH.read_text(encoding="utf-8"))
        return data, data["services"]["lullabeast"]

    def test_parses_single_service_distinct_project(self):
        data, _ = self._svc()
        assert list(data["services"].keys()) == ["lullabeast"]
        # A distinct compose project name keeps containers/networks separate
        # from the user stack, so both run side by side.
        assert data["name"] == "lullabeast-dev"

    def test_repo_bind_mounted_writable_at_app(self):
        # The whole point of the dev stack: the working tree IS /app.
        _, svc = self._svc()
        assert "..:/app" in svc["volumes"]

    def test_separate_state_volume_and_projects_dir(self):
        data, svc = self._svc()
        assert "lullabeast-dev-data:/data" in svc["volumes"]
        assert "./projects-dev:/data/projects" in svc["volumes"]
        # Volume name pinned (no compose project prefix) so migration/backup
        # commands can address it directly.
        assert data["volumes"]["lullabeast-dev-data"]["name"] == "lullabeast-dev-data"

    def test_ports_loopback_only_and_disjoint_from_user_stack(self):
        _, svc = self._svc()
        ports = [str(p) for p in svc["ports"]]
        assert len(ports) == 2
        assert all(p.startswith("127.0.0.1:") for p in ports)
        # Defaults 28790/28789 cannot collide with the user stack's
        # 18790/18789; the gateway maps onto the fixed in-container 18789.
        assert any("28790" in p for p in ports)
        assert any(p.endswith(":18789") and "28789" in p for p in ports)

    def test_dev_mode_and_port_env_wired(self):
        _, svc = self._svc()
        env = svc["environment"]
        assert env["DEV_MODE"] == "1"
        assert "DEV_UI_PORT" in str(env["UI_PORT"])
        assert "DEV_GATEWAY_PORT" in str(env["GATEWAY_PUBLISHED_PORT"])

    def test_hardening_parity_with_user_stack(self):
        _, svc = self._svc()
        assert svc["cap_drop"] == ["ALL"]
        assert "cap_add" not in svc
        assert "no-new-privileges:true" in svc["security_opt"]
        assert not svc.get("read_only", False)

    def test_env_file_shared_and_optional(self):
        _, svc = self._svc()
        assert svc["env_file"] == [{"path": ".env", "required": False}]

    def test_host_gateway_bridge_and_init(self):
        _, svc = self._svc()
        assert "host.docker.internal:host-gateway" in svc["extra_hosts"]
        assert svc["init"] is True

    def test_distinct_image_tag(self):
        # A distinct tag keeps a stale dev build from masquerading as the
        # user image (and vice versa); both build from the same Dockerfile.
        _, svc = self._svc()
        assert svc["image"] == "lullabeast:dev"
        assert svc["pull_policy"] == "build"
        assert svc["build"]["dockerfile"] == "deploy/Dockerfile"

    def test_projects_dev_gitignored(self):
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "deploy/projects-dev/x"],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0, "deploy/projects-dev/ must be gitignored"


class TestDevMode:
    """Entrypoint DEV_MODE behavior + the container-owned config keys that
    make a bind-mounted working tree safe to boot."""

    def test_dev_mode_defaults_off(self):
        assert 'DEV_MODE="${DEV_MODE:-0}"' in ENTRYPOINT

    def test_env_keys_forced_not_merged(self):
        # A dev bind mount brings the working tree's gitignored .env into the
        # container carrying bare-metal paths and stale tokens; merge-only
        # seeding would keep them and poison every derived path. The
        # container-owned keys must be overwritten every boot.
        assert "force_dotenv_keys" in ENTRYPOINT
        assert "merge_dotenv_missing_keys" not in ENTRYPOINT

    def test_ui_config_container_structural_keys_forced(self):
        # Same reasoning for ui/config.json: these keys are container truth.
        for needle in (
            'cfg["autodev_repo_path"]',
            'cfg["openclaw_root"]',
            'cfg["autodev_pipeline_root"]',
            'cfg["hooks_url"]',
            'cfg["gateway_published_port"]',
        ):
            assert needle in ENTRYPOINT, f"entrypoint must seed {needle}"

    def test_uvicorn_reload_only_in_dev_mode(self):
        assert 'UVICORN_ARGS=(--host 0.0.0.0 --port "$UI_PORT")' in ENTRYPOINT
        m = re.search(
            r'if \[ "\$DEV_MODE" = "1" \];.*?UVICORN_ARGS\+=\(--reload',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "--reload must be added only behind the DEV_MODE gate"

    def test_dev_deps_installed_only_in_dev_mode(self):
        m = re.search(
            r'if \[ "\$DEV_MODE" = "1" \];.*?requirements-dev\.txt',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "requirements-dev.txt install must be behind the DEV_MODE gate"

    def test_gateway_link_follows_published_port(self):
        # The banner and Settings link must point at the host-published
        # gateway port; inside the container the gateway stays on 18789.
        assert 'GATEWAY_LINK_PORT="${GATEWAY_PUBLISHED_PORT:-$GATEWAY_PORT}"' in ENTRYPOINT
        assert "http://127.0.0.1:${GATEWAY_LINK_PORT}" in ENTRYPOINT

    def test_control_ui_origins_extended_for_published_port(self):
        # A remapped gateway port needs its origin allow-listed or the
        # Control UI session hits an origin prompt. Guarded so the default
        # port adds nothing.
        assert "allowedOrigins" in ENTRYPOINT
        assert 'published != "18789"' in ENTRYPOINT


class TestGitAttributes:
    """Without .gitattributes, a Windows checkout (autocrlf=true)
    writes CRLF into shell scripts and .env.example, breaking the shebang and
    value parsing in-container."""

    GITATTRIBUTES = REPO_ROOT / ".gitattributes"

    def test_gitattributes_exists(self):
        assert self.GITATTRIBUTES.is_file()

    def test_forces_lf_for_shell_scripts_and_env_example(self):
        text = self.GITATTRIBUTES.read_text(encoding="utf-8")
        assert re.search(r"^\*\.sh text eol=lf$", text, re.MULTILINE)
        assert re.search(r"^\.env\.example text eol=lf$", text, re.MULTILINE)


class TestEnvExample:
    def test_every_contract_variable_documented(self):
        for name in (
            "OPENROUTER_API_KEY",
            "LOCAL_MODEL_URL",
            "UI_PORT",
            "GIT_USER_NAME",
            "GIT_USER_EMAIL",
            *TEMPLATE_MODEL_DEFAULTS,
        ):
            assert name in ENV_EXAMPLE, f".env.example is missing {name}"

    def test_anthropic_not_offered_in_env_example(self):
        assert "ANTHROPIC_API_KEY" not in ENV_EXAMPLE

    def test_model_defaults_match_template_module(self):
        # The commented defaults shown to users must be the audited picks.
        for name, default in TEMPLATE_MODEL_DEFAULTS.items():
            assert f"#{name}={default}" in ENV_EXAMPLE

    def test_local_model_url_documented(self):
        # v1.0.0 Phase 3, B4: the local-model knob is documented as a commented
        # example in the host.docker.internal form, with the local/<id> role
        # wiring hint and a pointer to the deploy README.
        assert "#LOCAL_MODEL_URL=http://host.docker.internal:11434" in ENV_EXAMPLE
        assert "Local models on the host" in ENV_EXAMPLE
        assert "local/" in ENV_EXAMPLE

    def test_no_dashes_in_new_local_model_prose(self):
        # Repo style: no em/en dashes in .env.example prose (the box-drawing
        # section rulers are the pre-existing style and are exempt).
        for line in ENV_EXAMPLE.splitlines():
            if "─" in line:  # box-drawing ruler, exempt
                continue
            assert "–" not in line and "—" not in line, (
                f"em/en dash in .env.example prose: {line}"
            )

    def test_no_committed_values(self):
        # No committed secret VALUES. A bare "NAME=" line (empty value, so key
        # entry is paste-only) is allowed; a non-comment line carrying a
        # non-empty value after "=" would be a committed secret and fails.
        for line in ENV_EXAMPLE.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert "=" in stripped, f"non-comment, non-assignment line: {line}"
            value = stripped.split("=", 1)[1].strip()
            assert value == "", f"committed value in .env.example: {line}"

    def test_deploy_env_is_gitignored(self):
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "deploy/.env"],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0, "deploy/.env must be gitignored"


class TestHardening:
    """Container-security posture lints (static; runtime acceptance is
    the operator's docker run)."""

    def test_app_copy_is_root_owned(self):
        # /app must be root-owned so the runtime user cannot modify the
        # orchestrator, gate scripts, server, or installer. A --chown on the
        # repo COPY would hand the whole tree to lullabeast.
        assert re.search(r"^COPY \. /app$", DOCKERFILE, re.MULTILINE)
        assert "COPY --chown" not in DOCKERFILE

    def test_app_write_islands(self):
        # The three per-boot write islands install.sh and the entrypoint
        # depend on: the plugin rebuild tree, plus sticky group-writable
        # /app and /app/ui for .env, .autodev/ and ui/config.json.
        assert "chown -R lullabeast:lullabeast /app/autodev/plugin" in DOCKERFILE
        assert "chgrp lullabeast /app /app/ui" in DOCKERFILE
        assert "chmod 1775 /app /app/ui" in DOCKERFILE

    def test_no_sudo_in_image(self):
        assert re.search(r"apt-get purge -y --auto-remove sudo", DOCKERFILE)
        assert "/etc/sudoers" in DOCKERFILE

    def test_pycache_redirected_off_app(self):
        # Python skips bytecode caching silently when the source dir is
        # unwritable; the prefix keeps caches working under /tmp.
        assert re.search(
            r"^ENV PYTHONPYCACHEPREFIX=/tmp/\S+$", DOCKERFILE, re.MULTILINE
        )

    def test_compose_no_new_privileges(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        assert "no-new-privileges:true" in data["services"]["lullabeast"]["security_opt"]

    def test_compose_drops_all_caps_and_adds_none_back(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        svc = data["services"]["lullabeast"]
        assert svc["cap_drop"] == ["ALL"]
        # Any add-back requires a documented probe (see the inline compose
        # comment); none is the shipped default.
        assert "cap_add" not in svc

    def test_read_only_rootfs_stays_off(self):
        # Assessed and deliberately OFF: install.sh writes
        # inside /app on every boot by contract, so read_only: true breaks
        # boot. If that contract ever changes, remove this test deliberately
        # along with the compose comment.
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        assert not data["services"]["lullabeast"].get("read_only", False)

    def test_deploy_gitignore_covers_env_and_projects(self):
        lines = [
            ln.strip()
            for ln in (DEPLOY / ".gitignore").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert ".env" in lines
        assert "projects/" in lines
        # And git actually honors it for the projects dir (deploy/.env is
        # asserted in TestEnvExample).
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "deploy/projects/x"],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0, "deploy/projects/ must be gitignored"

    def test_ui_config_json_excluded_from_build_context(self):
        # A dev tree's ui/config.json carries a real hooks_token; baking it
        # would embed the secret in a published image.
        lines = [
            ln.strip()
            for ln in (DEPLOY / "Dockerfile.dockerignore")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert "ui/config.json" in lines

    def test_no_secret_values_echoed(self):
        # The only secret the boot log may carry is the dashboard URL with
        # AUTODEV_UI_TOKEN (deliberate: docker compose logs must be able to
        # recover dashboard access). The API key and the hooks/gateway
        # tokens must never be interpolated into an echo/say/die line.
        for line in ENTRYPOINT.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(r"(echo|say|die)\b", stripped):
                assert not re.search(
                    r"\$\{?(ANTHROPIC_API_KEY|OPENROUTER_API_KEY|HOOKS_TOKEN|GATEWAY_TOKEN|AUTODEV_HOOKS_TOKEN)\b",
                    stripped,
                ), f"secret value interpolated into a log line: {line}"

    def test_security_md_container_subsection(self):
        text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        assert "### Container deployment" in text
        assert "deploy/README.md" in text

    def test_deploy_readme_hardening_sections(self):
        text = (DEPLOY / "README.md").read_text(encoding="utf-8")
        for needle in (
            "Security hardening",
            "What the sandbox does and does not contain",
            "Read-only rootfs",
            "Secrets posture",
            "no-new-privileges",
        ):
            assert needle in text, f"deploy/README.md is missing: {needle}"


class TestOfflineMode:
    """OFFLINE=1 boots the full stack keyless for CI smoke runs."""

    def test_offline_default_off(self):
        assert 'OFFLINE="${OFFLINE:-0}"' in ENTRYPOINT

    def test_offline_skips_api_key_requirement(self):
        # The key die must sit in the elif behind the OFFLINE gate, so
        # OFFLINE=1 boots keyless and every other boot still fails fast.
        m = re.search(
            r'if \[ "\$OFFLINE" = "1" \];.*?elif \[ -z "\$\{ANTHROPIC_API_KEY:-\}" \]',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "API-key die must be the elif branch of the OFFLINE gate"

    def test_offline_banner_is_loud_and_honest(self):
        # The roadmap requires a loud banner naming this as CI/smoke only.
        assert "OFFLINE=1: CI/smoke mode." in ENTRYPOINT
        assert "CI image smoke tests only" in ENTRYPOINT

    def test_offline_skips_live_probe_and_preserves_first_boot_marker(self):
        # OFFLINE must never fire the billable --live ping AND must leave the
        # first-boot marker unwritten so a later real boot still gets its one
        # ping: the OFFLINE no-op branch must guard the marker branch.
        m = re.search(
            r'if \[ "\$OFFLINE" = "1" \];.*?elif \[ ! -f "\$FIRST_BOOT_MARKER" \];'
            r'.*?--live.*?touch "\$FIRST_BOOT_MARKER"',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "--live/marker branch must be gated behind the OFFLINE no-op"


class TestSetupMode:
    """Keyless boot (not OFFLINE) becomes SETUP MODE instead of die."""

    def test_no_die_on_missing_key(self):
        # The old hard-exit is gone: a keyless, non-OFFLINE boot must never
        # `die` on the provider key. Setup mode replaces it.
        assert 'die "no provider' not in ENTRYPOINT
        assert not re.search(r"die .*no provider API key set", ENTRYPOINT)

    def test_setup_mode_branch_sets_flag_and_loud_banner(self):
        # The keyless branch (behind the OFFLINE gate) sets SETUP_MODE=1 and
        # prints a loud, honest banner naming the dashboard as the key surface.
        assert "SETUP_MODE=1" in ENTRYPOINT
        assert "SETUP MODE" in ENTRYPOINT
        m = re.search(
            r'if \[ "\$OFFLINE" = "1" \];.*?'
            r'elif \[ -z "\$\{ANTHROPIC_API_KEY:-\}" \].*?SETUP_MODE=1',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "keyless setup-mode branch must be the elif of the OFFLINE gate"

    def test_provider_env_loaded_per_variable_with_env_pinned(self):
        # provider.env is loaded per variable: anything deploy/.env sets at
        # boot is pinned for the container's lifetime, every other assignment
        # applies, and the apply path re-reads the file so dashboard edits
        # land. The old whole-file source (all-or-nothing: a keyed install
        # silently ignored every dashboard-written knob) is gone.
        assert 'PROVIDER_KEY_FILE="$DATA/secrets/provider.env"' in ENTRYPOINT
        assert "load_provider_env() {" in ENTRYPOINT
        assert "ENV_PINNED_VARS" in ENTRYPOINT
        assert '. "$PROVIDER_KEY_FILE"' not in ENTRYPOINT, (
            "the whole-file source must be replaced by load_provider_env"
        )
        # The shell-source-suggestive name must not creep back in and invite a
        # real `. provider.env`; the file is parsed, never executed.
        assert "source_provider_env" not in ENTRYPOINT

    def test_provider_env_reset_clears_non_pinned_before_reread(self):
        # A knob the dashboard dropped must stop applying on a live apply,
        # matching a clean boot: the loader unsets every non-pinned provider var
        # before it reads the file, and does so before the empty-file early
        # return so clearing still happens when the file is now empty.
        m = re.search(
            r'load_provider_env\(\) \{.*?for _v in \$PROVIDER_ENV_VARS; do'
            r'.*?ENV_PINNED_VARS.*?unset "\$_v".*?done'
            r'.*?\[ -s "\$PROVIDER_KEY_FILE" \] \|\| return 0',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "the loader must unset non-pinned provider vars before re-reading the file"

    def test_pinned_model_knobs_exported_for_dashboard(self):
        # The boot-time set of deploy/.env-pinned *_MODEL knobs is exported so
        # the dashboard can tell the operator a role is env-pinned, instead of
        # letting a Settings save look successful and then silently revert on
        # the post-apply re-read. Derived from ENV_PINNED_VARS (captured before
        # load_provider_env mixes provider.env values into the environment).
        assert "export AUTODEV_PINNED_MODEL_KNOBS=" in ENTRYPOINT
        m = re.search(
            r'AUTODEV_PINNED_MODEL_KNOBS="".*?for _v in .*?ROADMAP_MODEL.*?do'
            r'.*?ENV_PINNED_VARS.*?done.*?export AUTODEV_PINNED_MODEL_KNOBS=',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "pinned model knobs must be derived from ENV_PINNED_VARS and exported"

    def test_provider_env_parser_is_allowlisted(self):
        # The file is parsed line by line and only variables the dashboard may
        # write are applied; it is never executed as shell.
        assert "PROVIDER_ENV_VARS=" in ENTRYPOINT
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "LOCAL_MODEL_URL",
            "LOCAL_MODEL_TUNING_TARGET",
            "PROVIDER_SETUP_SKIPPED",
            *TEMPLATE_MODEL_DEFAULTS,
        ):
            assert var in ENTRYPOINT, f"provider.env allowlist is missing {var}"

    def test_local_tuning_overrides_scoped_to_tuning_target(self):
        # Stage D: with per-role local assignments, the confirm-field overrides
        # apply only to the model the user confirmed (LOCAL_MODEL_TUNING_TARGET);
        # without the target line, all role-referenced models, as before.
        assert 'os.environ.get("LOCAL_MODEL_TUNING_TARGET")' in ENTRYPOINT
        assert "targets = [tuning_target] if tuning_target else [" in ENTRYPOINT

    def test_provider_env_parser_strips_trailing_cr(self):
        # A CRLF provider.env must not export values with an invisible
        # trailing \r (an API key that "looks right" but fails auth).
        assert "val=\"${val%$'\\r'}\"" in ENTRYPOINT
        idx = ENTRYPOINT.find("val=\"${val%$'\\r'}\"")
        export_idx = ENTRYPOINT.find('export "$key=$val"')
        assert idx != -1 and export_idx != -1 and idx < export_idx, (
            "the CR trim must happen before the value is exported"
        )

    def test_setup_marker_written_and_cleared(self):
        # The marker is written after the /data mkdirs when in setup mode, and
        # removed when a key is present (clearing a stale marker on a keyed boot).
        assert 'SETUP_MARKER="$DATA/.setup-mode"' in ENTRYPOINT
        assert 'touch "$SETUP_MARKER"' in ENTRYPOINT
        assert 'rm -f "$SETUP_MARKER"' in ENTRYPOINT
        # Marker write must come after the /data mkdir (the dir must exist).
        mkdir_idx = ENTRYPOINT.find('mkdir -p "$DATA/secrets"')
        marker_idx = ENTRYPOINT.find('SETUP_MARKER="$DATA/.setup-mode"')
        assert mkdir_idx != -1 and marker_idx != -1
        assert mkdir_idx < marker_idx

    def test_setup_mode_defers_live_doctor(self):
        # In setup mode the first-boot --live doctor is deferred exactly like
        # OFFLINE: the OFFLINE-or-SETUP no-op branch guards the --live/marker
        # branch, so the marker stays unwritten until the key arrives.
        m = re.search(
            r'if \[ "\$OFFLINE" = "1" \] \|\| \[ "\$SETUP_MODE" = "1" \];'
            r'.*?elif \[ ! -f "\$FIRST_BOOT_MARKER" \];.*?--live.*?touch "\$FIRST_BOOT_MARKER"',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "setup mode must defer the --live ping like OFFLINE"

    def test_setup_unlock_restarts_gateway(self):
        # The watch loop must restart the gateway when the key lands so
        # OpenClaw reads it from its process environment (the unlock mechanism).
        m = re.search(
            r'if \[ "\$SETUP_MODE" = "1" \] && \[ -s "\$PROVIDER_KEY_FILE" \];.*?'
            r'load_provider_env.*?stop_gateway.*?start_gateway.*?wait_for_gateway',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "the watch loop must load the key and restart the gateway on unlock"

    def test_local_model_url_in_setup_gate(self):
        # v1.0.0 Phase 3, B2: setup mode is entered only when there is no cloud
        # key AND no LOCAL_MODEL_URL (evaluated after provider.env is loaded).
        m = re.search(
            r'elif \[ -z "\$\{ANTHROPIC_API_KEY:-\}" \] && '
            r'\[ -z "\$\{OPENROUTER_API_KEY:-\}" \] \\?\s*'
            r'&& \[ -z "\$\{LOCAL_MODEL_URL:-\}" \];.*?SETUP_MODE=1',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "the setup-mode elif must gate on LOCAL_MODEL_URL too"

    def test_setup_banner_mentions_local_model_alternative(self):
        # The setup banner must name a local model server as an alternative to a
        # cloud key (dashboard or LOCAL_MODEL_URL in deploy/.env).
        assert "local model server is an alternative" in ENTRYPOINT
        assert "LOCAL_MODEL_URL" in ENTRYPOINT

    def test_watch_loop_reruns_config_wiring_before_gateway_restart(self):
        # A dashboard-wired *_MODEL / LOCAL_MODEL_URL must land in openclaw.json
        # before the gateway restarts, so the apply pass re-runs both wiring
        # functions after re-reading provider.env (marker consumed first).
        m = re.search(
            r'if \[ "\$APPLY" = "1" \];.*?rm -f "\$APPLY_REQUEST_FILE".*?'
            r'load_provider_env.*?'
            r'render_reconcile_config.*?wire_or_probe_local_models.*?'
            r'stop_gateway.*?start_gateway.*?wait_for_gateway',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "the apply pass must re-run config wiring before the gateway restart"

    def test_api_key_silently_accepted_comment(self):
        # B1-enforcement: ANTHROPIC_API_KEY is deliberately still accepted but
        # never promoted; the entrypoint carries that decision as a comment.
        assert re.search(
            r"#.*ANTHROPIC_API_KEY.*(accepted|B1-enforcement)", ENTRYPOINT
        ), "entrypoint must comment that ANTHROPIC_API_KEY is silently accepted"

    def test_watch_loop_checks_supervised_pids(self):
        # The lifetime watch loop owns supervision: kill -0 liveness on each
        # pid, collect the exit status, tear down with it.
        m = re.search(
            r'while :; do.*?kill -0 "\$pid".*?wait "\$pid".*?shutdown.*?exit "\$rc"',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "watch loop must monitor supervised pids and exit with their rc"

    def test_setup_watch_loop_clears_marker_on_unlock(self):
        # After the deferred doctor passes, the loop clears the setup marker and
        # falls through to normal supervision.
        unlock_idx = ENTRYPOINT.find('say "provider key received from the dashboard')
        assert unlock_idx != -1
        rm_idx = ENTRYPOINT.find('rm -f "$SETUP_MARKER"', unlock_idx)
        assert rm_idx != -1, "the unlock path must clear the setup marker"


class TestApplyWatch:
    """The watch loop runs for the container's lifetime: it supervises the
    four processes AND applies dashboard configuration changes (the
    apply-request marker protocol), not just first-run setup."""

    def test_apply_request_file_defined_under_secrets(self):
        assert 'APPLY_REQUEST_FILE="$DATA/secrets/apply.request"' in ENTRYPOINT

    def test_stale_marker_cleared_before_the_watch_loop(self):
        # A marker surviving a container restart must not fire a redundant
        # apply pass: boot already renders config and restarts the gateway.
        rm_idx = ENTRYPOINT.find('rm -f "$APPLY_REQUEST_FILE"')
        loop_idx = ENTRYPOINT.find("while :; do")
        assert rm_idx != -1 and loop_idx != -1
        assert rm_idx < loop_idx

    def test_marker_consumed_before_apply_work_starts(self):
        # Consume-first ordering: a request landing mid-apply coalesces into
        # one more idempotent pass instead of being lost.
        apply_idx = ENTRYPOINT.find('if [ "$APPLY" = "1" ]; then')
        assert apply_idx != -1
        consume_idx = ENTRYPOINT.find('rm -f "$APPLY_REQUEST_FILE"', apply_idx)
        load_idx = ENTRYPOINT.find("load_provider_env", apply_idx)
        assert consume_idx != -1 and load_idx != -1
        assert consume_idx < load_idx

    def test_watch_loop_replaces_wait_n(self):
        # One lifetime loop supervises and applies; the blocking `wait -n`
        # cannot host the apply trigger and is gone.
        assert "wait -n" not in ENTRYPOINT

    def test_post_setup_apply_doctor_is_advisory(self):
        # A bad settings save must not tear down a container that may have
        # queued work: the post-setup apply runs the non-live doctor and
        # warns; only setup unlock keeps its fatal deferred --live doctor.
        needle = "doctor reports failing checks after config apply"
        idx = ENTRYPOINT.find(needle)
        assert idx != -1
        line = ENTRYPOINT[ENTRYPOINT.rfind("\n", 0, idx) : idx]
        assert "say" in line and "die" not in line

    def test_apply_path_gateway_restart_is_non_fatal(self):
        # A bad save must not tear down a reachable container: the apply-path
        # gateway wait is advisory (warn + stay up), while boot keeps the fatal
        # default so a compose restart remains its recovery.
        m = re.search(
            r'if \[ "\$APPLY" = "1" \];.*?start_gateway\s*\n\s*wait_for_gateway advisory \|\| true',
            ENTRYPOINT,
            re.DOTALL,
        )
        assert m, "the apply-path gateway wait must be advisory (non-fatal)"
        # wait_for_gateway keeps a fatal default (die) for boot and only warns
        # via say in advisory mode.
        wfg = re.search(r'wait_for_gateway\(\) \{.*?\n\}', ENTRYPOINT, re.DOTALL)
        assert wfg, "wait_for_gateway definition not found"
        body = wfg.group(0)
        assert '"advisory"' in body and "say" in body and "die" in body

    def test_apply_logs_completion_needle(self):
        # The CI smoke greps docker logs for exactly this byte sequence.
        assert "configuration applied; gateway restarted" in ENTRYPOINT

    def test_apply_pass_never_runs_live_doctor(self):
        # The billable --live ping belongs to setup unlock only; the
        # post-setup apply branch runs the doctor without flags.
        apply_idx = ENTRYPOINT.find("doctor reports failing checks after config apply")
        assert apply_idx != -1
        # The doctor invocation feeding this warning must not carry --live.
        region = ENTRYPOINT[apply_idx - 600 : apply_idx]
        assert "python3 -m autodev.installer.doctor)" in region.replace("\n", "")


def _load_smoke_assert_module():
    # deploy/ is not a package (no __init__.py, and it must stay importable by
    # nothing at runtime); load the script by file path for the tests.
    import importlib.util

    spec = importlib.util.spec_from_file_location("deploy_smoke_assert", SMOKE_ASSERT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSmokeAssert:
    """Functional tests for deploy/smoke_assert.py against tmp fixtures."""

    def _run(self, tmp_path, checks):
        path = tmp_path / "doctor.json"
        path.write_text(json.dumps({"checks": checks}), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SMOKE_ASSERT), str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _green_checks(self):
        mod = _load_smoke_assert_module()
        REQUIRED_OK, REQUIRED_SKIPPED = mod.REQUIRED_OK, mod.REQUIRED_SKIPPED

        checks = [
            {"id": cid, "status": "ok", "detail": "", "fix_hint": ""}
            for cid in REQUIRED_OK
        ]
        checks += [
            {"id": cid, "status": "skipped", "detail": "", "fix_hint": ""}
            for cid in REQUIRED_SKIPPED
        ]
        checks.append(
            {"id": "python_deps", "status": "ok", "detail": "", "fix_hint": ""}
        )
        return checks

    def test_all_green_exits_zero(self, tmp_path):
        proc = self._run(tmp_path, self._green_checks())
        assert proc.returncode == 0, proc.stderr
        assert "SMOKE OK" in proc.stdout

    def test_any_fail_exits_nonzero_naming_the_check(self, tmp_path):
        checks = self._green_checks()
        checks.append(
            {
                "id": "plugin_deployed",
                "status": "fail",
                "detail": "marker missing",
                "fix_hint": "re-run install.sh",
            }
        )
        proc = self._run(tmp_path, checks)
        assert proc.returncode == 1
        # Acceptance: the doctor check is NAMED in the failure output.
        assert "plugin_deployed" in proc.stderr
        assert "marker missing" in proc.stderr

    def test_required_check_warn_is_not_good_enough(self, tmp_path):
        checks = self._green_checks()
        for c in checks:
            if c["id"] == "secret_sync":
                c["status"] = "warn"
        proc = self._run(tmp_path, checks)
        assert proc.returncode == 1
        assert "secret_sync" in proc.stderr

    def test_required_check_missing_is_flagged(self, tmp_path):
        checks = [c for c in self._green_checks() if c["id"] != "gateway_up"]
        proc = self._run(tmp_path, checks)
        assert proc.returncode == 1
        assert "gateway_up" in proc.stderr

    def test_webhook_ping_must_be_skipped_not_ok_or_fail(self, tmp_path):
        checks = self._green_checks()
        for c in checks:
            if c["id"] == "webhook_ping":
                c["status"] = "fail"
        proc = self._run(tmp_path, checks)
        assert proc.returncode == 1
        assert "webhook_ping" in proc.stderr

    def test_unparseable_report_exits_nonzero(self, tmp_path):
        path = tmp_path / "doctor.json"
        path.write_text("{not json", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SMOKE_ASSERT), str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 1
        assert "unreadable/unparseable" in proc.stderr

    def test_required_ids_exist_in_the_doctor_catalogue(self):
        # Drift guard: every id smoke_assert requires must be a real doctor
        # check (check_<id> function), or a doctor rename silently turns the
        # CI assertion into a guaranteed "missing check" failure.
        from autodev.installer import doctor

        mod = _load_smoke_assert_module()
        for cid in (*mod.REQUIRED_OK, *mod.REQUIRED_SKIPPED):
            assert hasattr(doctor, f"check_{cid}"), (
                f"smoke_assert requires unknown doctor check id: {cid}"
            )


class TestDeployImageWorkflow:
    """Static lints on .github/workflows/deploy-image.yml."""

    def _load(self):
        return yaml.safe_load(WORKFLOW_TEXT)

    def _triggers(self):
        data = self._load()
        # YAML 1.1 parses the bare key `on` as boolean True.
        return data.get("on") or data[True]

    ROADMAP_PATHS = (
        "deploy/**",
        "install.sh",
        "requirements*.txt",
        "ui/requirements.txt",
        "autodev/plugin/**",
        "autodev/installer/**",
    )

    def test_workflow_exists_and_parses(self):
        assert WORKFLOW_PATH.is_file()
        assert isinstance(self._load(), dict)

    def test_push_and_pr_path_filters_cover_the_deploy_surface(self):
        trig = self._triggers()
        for event in ("push", "pull_request"):
            paths = trig[event]["paths"]
            for p in self.ROADMAP_PATHS:
                assert p in paths, f"{event} paths filter is missing {p}"
            # The workflow must rebuild when it itself changes.
            assert ".github/workflows/deploy-image.yml" in paths

    def test_push_covers_main_and_version_tags(self):
        push = self._triggers()["push"]
        assert "main" in push["branches"]
        assert "v*" in push["tags"]

    def test_both_task0_variants_built(self):
        # Baked default (no build-arg override) plus the no-bake variant
        # (empty OPENCLAW_VERSION build arg).
        assert "docker build -f deploy/Dockerfile -t lullabeast:ci ." in WORKFLOW_TEXT
        assert "--build-arg OPENCLAW_VERSION=" in WORKFLOW_TEXT

    def test_smoke_boots_offline_and_asserts_doctor_json(self):
        assert "OFFLINE=1" in WORKFLOW_TEXT
        assert "autodev.installer.doctor --json" in WORKFLOW_TEXT
        assert "deploy/smoke_assert.py" in WORKFLOW_TEXT

    def test_smoke_exercises_config_apply_cycle(self):
        # The lifetime watch loop only exists at runtime: the smoke touches
        # the apply marker in the running container and waits for the apply
        # completion line, proving re-render + gateway restart end to end.
        assert "apply.request" in WORKFLOW_TEXT
        assert "configuration applied; gateway restarted" in WORKFLOW_TEXT

    def test_publish_gated_on_version_tags_with_packages_permission(self):
        jobs = self._load()["jobs"]
        publish = jobs["publish"]
        assert "startsWith(github.ref, 'refs/tags/v')" in publish["if"]
        assert publish["permissions"]["packages"] == "write"
        assert set(publish["needs"]) == {"build-smoke", "build-no-bake"}

    def test_publish_pushes_ghcr_lullabeast_tag_and_latest(self):
        assert "ghcr.io" in WORKFLOW_TEXT
        assert "/lullabeast" in WORKFLOW_TEXT
        assert re.search(r"docker push .*:\$\{GITHUB_REF_NAME\}", WORKFLOW_TEXT)
        assert re.search(r'docker push .*:latest', WORKFLOW_TEXT)

    def test_smoke_boots_with_shipped_hardening_posture(self):
        # The smoke boots with the SHIPPED posture (mirrors docker-compose.yml),
        # not the docker default, so it exercises the sandbox rather than the text.
        assert "--cap-drop ALL" in WORKFLOW_TEXT
        assert "no-new-privileges" in WORKFLOW_TEXT

    def test_smoke_asserts_runtime_sandbox(self):
        # A runtime probe confirms code is read-only to the runtime user (an
        # existing root-owned file, not a bare new file in sticky /app) and that
        # the capability bounding set is empty.
        assert "/app/ui/server.py" in WORKFLOW_TEXT
        assert "CapBnd" in WORKFLOW_TEXT
        assert "0000000000000000" in WORKFLOW_TEXT

    def test_publish_reuses_smoked_image_not_a_rebuild(self):
        # publish pushes the exact bytes build-smoke smoked: build-smoke saves
        # and uploads the image, publish downloads and docker-loads it, and the
        # publish steps contain no docker build.
        jobs = self._load()["jobs"]
        smoke_steps = jobs["build-smoke"]["steps"]
        assert any(str(s.get("uses", "")).startswith("actions/upload-artifact") for s in smoke_steps)
        assert "docker save lullabeast:ci" in WORKFLOW_TEXT
        publish = jobs["publish"]
        assert any(str(s.get("uses", "")).startswith("actions/download-artifact") for s in publish["steps"])
        publish_run = "\n".join(str(s.get("run", "")) for s in publish["steps"])
        assert "docker load" in publish_run
        assert "docker build" not in publish_run

    def test_no_floating_openclaw_version(self):
        # Cross-cutting risk 1: never float "latest" for OpenClaw anywhere.
        assert "openclaw@latest" not in WORKFLOW_TEXT

    def test_no_em_or_en_dashes(self):
        assert "—" not in WORKFLOW_TEXT and "–" not in WORKFLOW_TEXT


class TestDocs:
    def test_eval_migration_doc_covers_required_contract(self):
        text = (DEPLOY / "EVAL-MIGRATION.md").read_text(encoding="utf-8")
        for needle in (
            "pipeline-project",
            "_pipeline_symlink_paths",
            "OPENCLAW_ROOT",
            "AUTODEV_PIPELINE_ROOT",
            "/data/openclaw",
            "pipeline_state.json",
            "pipeline_queue.json",
            "pipeline_events.jsonl",
            "metrics_history",
            "sessions",
            "18789",
            "18790",
            "docker compose exec",
            "AUTODEV_UI_TOKEN",
            "hooks.token",
            "guest mode",
        ):
            assert needle in text, f"EVAL-MIGRATION.md is missing: {needle}"

    def test_deploy_readme_covers_required_sections(self):
        text = (DEPLOY / "README.md").read_text(encoding="utf-8")
        for needle in (
            "MIT",  # licensing note
            "Quickstart",
            "Upgrade procedure",
            "NFS",  # flock caveat
            "Spend warning",
            "$0",  # cost note
            "Customization",
        ):
            assert needle in text, f"deploy/README.md is missing: {needle}"

    def test_no_em_or_en_dashes_in_new_deploy_files(self):
        # Repo prose rule: no em/en dashes in new docs or user-facing copy.
        for name in (
            "Dockerfile",
            "entrypoint.sh",
            "docker-compose.yml",
            "docker-compose.dev.yml",
            ".env.example",
            "Dockerfile.dockerignore",
            ".gitignore",
            "README.md",
            "EVAL-MIGRATION.md",
            "smoke_assert.py",
        ):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            assert "—" not in text and "–" not in text, (
                f"deploy/{name} contains an em/en dash"
            )

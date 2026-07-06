"""Static lints for the DS-3/DS-4/DS-5 container deploy files.

Hermetic by construction: every test reads repo files (or runs `bash -n` /
`git check-ignore` / `python deploy/smoke_assert.py` against tmp fixtures);
nothing touches ~/.openclaw, the live .autodev tree, or the network. Real
`docker build` / `docker compose up` runs are manual acceptance (and DS-5 CI);
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
        # render_template_text (DS-2b contract), never a hand-rolled sed.
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


class TestCompose:
    def test_parses_and_single_service(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        assert list(data["services"].keys()) == ["lullabeast"]

    def test_ui_port_published_loopback_only(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        ports = data["services"]["lullabeast"]["ports"]
        assert len(ports) == 1
        assert str(ports[0]).startswith("127.0.0.1:")

    def test_gateway_port_not_published(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        ports = data["services"]["lullabeast"]["ports"]
        assert not any("18789" in str(p) for p in ports)

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

    def test_env_file_wired(self):
        data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        assert data["services"]["lullabeast"]["env_file"] == ".env"


class TestEnvExample:
    def test_every_contract_variable_documented(self):
        for name in (
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "UI_PORT",
            "GIT_USER_NAME",
            "GIT_USER_EMAIL",
            *TEMPLATE_MODEL_DEFAULTS,
        ):
            assert name in ENV_EXAMPLE, f".env.example is missing {name}"

    def test_model_defaults_match_template_module(self):
        # The commented defaults shown to users must be the audited picks.
        for name, default in TEMPLATE_MODEL_DEFAULTS.items():
            assert f"#{name}={default}" in ENV_EXAMPLE

    def test_no_committed_values(self):
        # Every non-comment line must be blank; a real value in the example
        # would be a committed secret.
        for line in ENV_EXAMPLE.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raise AssertionError(f"uncommented assignment in .env.example: {line}")

    def test_deploy_env_is_gitignored(self):
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "deploy/.env"],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0, "deploy/.env must be gitignored"


class TestHardening:
    """DS-4 container-security posture lints (static; runtime acceptance is
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
        # Assessed and deliberately OFF (DS-4 task 3): install.sh writes
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
    """DS-5: OFFLINE=1 boots the full stack keyless for CI smoke runs."""

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


def _load_smoke_assert_module():
    # deploy/ is not a package (no __init__.py, and it must stay importable by
    # nothing at runtime); load the script by file path for the tests.
    import importlib.util

    spec = importlib.util.spec_from_file_location("deploy_smoke_assert", SMOKE_ASSERT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSmokeAssert:
    """DS-5: functional tests for deploy/smoke_assert.py against tmp fixtures."""

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
    """DS-5: static lints on .github/workflows/deploy-image.yml."""

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
            "MIT",  # Task 0 licensing note
            "Quickstart",
            "Upgrade procedure",
            "NFS",  # flock caveat
            "Spend warning",
            "$0",  # cost note (DS-2b)
            "Customization",
        ):
            assert needle in text, f"deploy/README.md is missing: {needle}"

    def test_no_em_or_en_dashes_in_new_deploy_files(self):
        # Repo prose rule: no em/en dashes in new docs or user-facing copy.
        for name in (
            "Dockerfile",
            "entrypoint.sh",
            "docker-compose.yml",
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

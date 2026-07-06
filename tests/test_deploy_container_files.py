"""Static lints for the DS-3 container deploy files.

Hermetic by construction: every test reads repo files (or runs `bash -n` /
`git check-ignore` against them read-only); nothing touches ~/.openclaw, the
live .autodev tree, or the network. Real `docker build` / `docker compose up`
runs are manual acceptance (and DS-5 CI); do not fake them here.
"""

import re
import subprocess
from pathlib import Path

import yaml

from autodev.installer.openclaw_template import TEMPLATE_MODEL_DEFAULTS

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "deploy"

DOCKERFILE = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = (DEPLOY / "entrypoint.sh").read_text(encoding="utf-8")
COMPOSE_PATH = DEPLOY / "docker-compose.yml"
ENV_EXAMPLE = (DEPLOY / ".env.example").read_text(encoding="utf-8")


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
            "README.md",
            "EVAL-MIGRATION.md",
        ):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            assert "—" not in text and "–" not in text, (
                f"deploy/{name} contains an em/en dash"
            )

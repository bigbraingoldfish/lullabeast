"""Tests for the dashboard MCP server (``autodev/mcp/dashboard_server.py``).

Covers the tool catalogue shape, the JSON-RPC handshake and stdio loop, the
tool→endpoint routing table (drift-guarded: every tool must appear), the
path-parameter and ``dashboard_get`` guards, HTTP error mapping, Bearer-token
transmission against a real in-process HTTP server, and configuration
resolution order.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autodev.mcp import dashboard_server as mcp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FAKE_CONFIG = {"base_url": "http://test.invalid", "token": "", "timeout": 1.0}


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

class TestToolCatalogue:
    def test_names_unique(self):
        names = [spec["name"] for spec in mcp.TOOL_SPECS]
        assert len(names) == len(set(names))

    def test_listing_shape(self):
        tools = mcp.tool_listing()
        assert tools, "catalogue must not be empty"
        for tool in tools:
            assert len(tool["description"]) >= 30, tool["name"]
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert set(schema["required"]) <= set(schema["properties"])
            assert set(tool["annotations"]) == {"readOnlyHint", "destructiveHint"}

    def test_read_only_tools_are_get(self):
        for spec in mcp.TOOL_SPECS:
            if spec["read_only"]:
                assert spec["method"] == "GET", spec["name"]
            if spec["method"] == "GET":
                assert spec["read_only"], spec["name"]

    def test_path_and_query_params_declared_in_properties(self):
        for spec in mcp.TOOL_SPECS:
            declared = set(spec["properties"])
            assert set(spec["path_params"]) <= declared, spec["name"]
            assert set(spec["query_params"]) <= declared, spec["name"]

    def test_escalation_commands_match_ui_server(self):
        # Drift guard against ui/server.py VALID_COMMANDS.
        with open(os.path.join(REPO_ROOT, "ui", "server.py"), encoding="utf-8") as fh:
            source = fh.read()
        for command in mcp.ESCALATION_COMMANDS:
            assert '"%s"' % command in source

    def test_phase_override_roles_match_ui_server(self):
        from ui import server as ui_server
        assert tuple(mcp.PHASE_OVERRIDE_ROLES) == tuple(ui_server._PHASE_OVERRIDE_ROLES)


# ---------------------------------------------------------------------------
# JSON-RPC handshake
# ---------------------------------------------------------------------------

class TestJsonRpc:
    def test_initialize_echoes_protocol_version(self):
        response = mcp.handle_message({
            "jsonrpc": "2.0", "id": 7, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        })
        assert response["id"] == 7
        assert response["result"]["protocolVersion"] == "2025-03-26"
        assert response["result"]["serverInfo"]["name"] == mcp.SERVER_NAME
        assert "tools" in response["result"]["capabilities"]

    def test_initialize_defaults_protocol_version(self):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert response["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION

    def test_ping(self):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert response["result"] == {}

    def test_tools_list(self):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        assert "pipeline_state" in names and "answer_escalation" in names

    def test_unknown_method_errors(self):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
        assert response["error"]["code"] == -32601

    def test_notifications_ignored(self):
        assert mcp.handle_message({"jsonrpc": "2.0",
                                   "method": "notifications/initialized"}) is None
        # Unknown *notification* (no id) is ignored, not errored.
        assert mcp.handle_message({"jsonrpc": "2.0", "method": "bogus/thing"}) is None


# ---------------------------------------------------------------------------
# Routing table — drift-guarded: every tool must be listed here
# ---------------------------------------------------------------------------

ROUTING = [
    ("pipeline_state", {}, "GET", "/api/state", {}, None),
    ("pipeline_events", {"limit": 5, "offset": 2}, "GET", "/api/events",
     {"limit": 5, "offset": 2}, None),
    ("pipeline_log_tail", {"lines": 100}, "GET", "/api/log/tail", {"lines": 100}, None),
    ("roadmap", {}, "GET", "/api/roadmap", {}, None),
    ("metrics_summary", {}, "GET", "/api/metrics-summary", {}, None),
    ("doctor_report", {}, "GET", "/api/doctor", {}, None),
    ("queue_list", {}, "GET", "/api/queue", {}, None),
    ("queue_status", {}, "GET", "/api/queue/status", {}, None),
    ("queue_snapshot", {"entry_id": "e1"}, "GET", "/api/queue/e1/snapshot", {}, None),
    ("queue_report", {"entry_id": "e1"}, "GET", "/api/queue/e1/report", {}, None),
    ("completion_report", {}, "GET", "/api/completion-report", {}, None),
    ("dashboard_get", {"path": "/api/setup/status", "query": {"a": 1}}, "GET",
     "/api/setup/status", {"a": 1}, None),
    ("answer_escalation", {"command": "PROCEED"}, "POST", "/api/command", {},
     {"command": "PROCEED"}),
    ("stop_pipeline", {}, "POST", "/api/stop", {}, None),
    ("resume_ready", {}, "POST", "/api/resume-ready", {}, None),
    ("resume_orchestrator", {}, "POST", "/api/resume-orchestrator", {}, None),
    # Endpoints declared with a JSON body always get one (FastAPI `request: dict`
    # handlers 422 on a missing body), even when every field is optional.
    ("git_recover", {}, "POST", "/api/pipeline/git-recover", {}, {}),
    ("launch_project", {"repo_path": "/tmp/x"}, "POST", "/api/setup/launch", {},
     {"repo_path": "/tmp/x"}),
    ("switch_project",
     {"repo_path": "/tmp/x", "start_orchestrator": True, "confirm_roadmap_archive": True},
     "POST", "/api/setup/switch-project", {},
     {"repo_path": "/tmp/x", "start_orchestrator": True, "confirm_roadmap_archive": True}),
    ("phase_model_overrides", {}, "GET", "/api/phase-model-override", {}, None),
    ("set_phase_model_override",
     {"raw_id": "CORE-1", "role": "executor", "model": "local/qwen"},
     "POST", "/api/phase-model-override", {},
     {"raw_id": "CORE-1", "role": "executor", "model": "local/qwen"}),
    # DELETE with a JSON body — the endpoint reads {raw_id, role?} from the body.
    ("clear_phase_model_override", {"raw_id": "CORE-1"}, "DELETE",
     "/api/phase-model-override", {}, {"raw_id": "CORE-1"}),
    ("queue_add", {"project_path": "/tmp/x"}, "POST", "/api/queue/add", {},
     {"project_path": "/tmp/x"}),
    ("queue_trigger_next", {}, "POST", "/api/queue/trigger-next", {}, None),
    ("queue_set_mode", {"queue_mode": "auto"}, "PATCH", "/api/queue/mode", {},
     {"queue_mode": "auto"}),
    ("queue_relaunch", {"entry_id": "e1"}, "POST", "/api/queue/e1/relaunch", {}, None),
    ("queue_revalidate", {"entry_id": "e1"}, "POST", "/api/queue/e1/revalidate", {}, None),
    ("queue_delete", {"entry_id": "e1"}, "DELETE", "/api/queue/e1", {}, None),
]


class TestRouting:
    def test_routing_table_covers_every_tool(self):
        assert {row[0] for row in ROUTING} == set(mcp.TOOLS_BY_NAME)

    @pytest.mark.parametrize("name,args,method,path,query,body",
                             ROUTING, ids=[row[0] for row in ROUTING])
    def test_route(self, name, args, method, path, query, body):
        spec = mcp.TOOLS_BY_NAME[name]
        assert mcp.build_http_call(spec, args) == (method, path, query, body)

    def test_entry_id_is_url_quoted(self):
        spec = mcp.TOOLS_BY_NAME["queue_snapshot"]
        _, path, _, _ = mcp.build_http_call(spec, {"entry_id": "a b"})
        assert path == "/api/queue/a%20b/snapshot"


# ---------------------------------------------------------------------------
# Local guards (never reach HTTP)
# ---------------------------------------------------------------------------

class TestGuards:
    def _no_http(self, monkeypatch):
        def _boom(*a, **k):  # pragma: no cover - would mean a guard leaked
            raise AssertionError("HTTP must not be reached")
        monkeypatch.setattr(mcp, "http_request", _boom)

    def test_missing_required_argument(self, monkeypatch):
        self._no_http(monkeypatch)
        result = mcp.call_tool("answer_escalation", {}, _FAKE_CONFIG)
        assert result["isError"]
        assert "command" in result["content"][0]["text"]

    def test_enum_violation(self, monkeypatch):
        self._no_http(monkeypatch)
        result = mcp.call_tool("answer_escalation", {"command": "FROB"}, _FAKE_CONFIG)
        assert result["isError"]

    def test_unknown_argument_rejected(self, monkeypatch):
        self._no_http(monkeypatch)
        result = mcp.call_tool("stop_pipeline", {"force": True}, _FAKE_CONFIG)
        assert result["isError"]

    def test_unknown_tool(self, monkeypatch):
        self._no_http(monkeypatch)
        result = mcp.call_tool("nonexistent_tool", {}, _FAKE_CONFIG)
        assert result["isError"]

    @pytest.mark.parametrize("entry_id", ["../etc", "a/b", ".", ".."])
    def test_path_param_traversal_rejected(self, monkeypatch, entry_id):
        self._no_http(monkeypatch)
        result = mcp.call_tool("queue_snapshot", {"entry_id": entry_id}, _FAKE_CONFIG)
        assert result["isError"]

    @pytest.mark.parametrize("path", [
        "/health", "api/state", "/api/../secret", "/api/state?x=1", "/api/state#f", "",
    ])
    def test_dashboard_get_path_guard(self, monkeypatch, path):
        self._no_http(monkeypatch)
        result = mcp.call_tool("dashboard_get", {"path": path}, _FAKE_CONFIG)
        assert result["isError"]

    def test_dashboard_get_query_must_be_object(self, monkeypatch):
        self._no_http(monkeypatch)
        result = mcp.call_tool("dashboard_get",
                               {"path": "/api/state", "query": "a=1"}, _FAKE_CONFIG)
        assert result["isError"]


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------

class TestResultMapping:
    def test_success_response(self, monkeypatch):
        monkeypatch.setattr(mcp, "http_request",
                            lambda *a, **k: (200, {"pipeline_status": "RUNNING"}))
        result = mcp.call_tool("pipeline_state", {}, _FAKE_CONFIG)
        assert not result["isError"]
        payload = json.loads(result["content"][0]["text"])
        assert payload == {"status": 200, "body": {"pipeline_status": "RUNNING"}}

    def test_http_409_is_error_with_detail(self, monkeypatch):
        monkeypatch.setattr(mcp, "http_request",
                            lambda *a, **k: (409, {"detail": "orchestrator running"}))
        result = mcp.call_tool("stop_pipeline", {}, _FAKE_CONFIG)
        assert result["isError"]
        payload = json.loads(result["content"][0]["text"])
        assert payload["status"] == 409
        assert "orchestrator running" in payload["body"]["detail"]

    def test_transport_failure_names_base_url(self, monkeypatch):
        def _refused(*a, **k):
            raise urllib.error.URLError(OSError(111, "Connection refused"))
        monkeypatch.setattr(mcp, "http_request", _refused)
        result = mcp.call_tool("pipeline_state", {}, _FAKE_CONFIG)
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "http://test.invalid" in text and "unreachable" in text


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

class TestConfigResolution:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for var in ("AUTODEV_UI_URL", "AUTODEV_UI_TOKEN", "AUTODEV_UI_PORT",
                    "UI_PORT", "AUTODEV_MCP_HTTP_TIMEOUT", "AUTODEV_REPO_PATH"):
            monkeypatch.delenv(var, raising=False)

    def test_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTODEV_REPO_PATH", str(tmp_path))  # no ui/config.json
        config = mcp.resolve_dashboard_config()
        assert config["base_url"] == "http://127.0.0.1:18790"
        assert config["token"] == ""
        assert config["timeout"] == mcp.DEFAULT_HTTP_TIMEOUT_SECONDS

    def test_ui_config_json_fallback(self, monkeypatch, tmp_path):
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "config.json").write_text(
            json.dumps({"port": 28790, "ui_token": "from-config"}))
        monkeypatch.setenv("AUTODEV_REPO_PATH", str(tmp_path))
        config = mcp.resolve_dashboard_config()
        assert config["base_url"] == "http://127.0.0.1:28790"
        assert config["token"] == "from-config"

    def test_env_wins_over_ui_config(self, monkeypatch, tmp_path):
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "config.json").write_text(
            json.dumps({"port": 28790, "ui_token": "from-config"}))
        monkeypatch.setenv("AUTODEV_REPO_PATH", str(tmp_path))
        monkeypatch.setenv("AUTODEV_UI_URL", "http://127.0.0.1:9999/")
        monkeypatch.setenv("AUTODEV_UI_TOKEN", "from-env")
        config = mcp.resolve_dashboard_config()
        assert config["base_url"] == "http://127.0.0.1:9999"  # trailing slash stripped
        assert config["token"] == "from-env"

    def test_garbage_port_and_timeout_fall_back(self, monkeypatch, tmp_path):
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "config.json").write_text(json.dumps({"port": "9 k"}))
        monkeypatch.setenv("AUTODEV_REPO_PATH", str(tmp_path))
        monkeypatch.setenv("AUTODEV_MCP_HTTP_TIMEOUT", "soon")
        config = mcp.resolve_dashboard_config()
        assert config["base_url"] == "http://127.0.0.1:18790"
        assert config["timeout"] == mcp.DEFAULT_HTTP_TIMEOUT_SECONDS

    def test_corrupt_ui_config_ignored(self, monkeypatch, tmp_path):
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "config.json").write_text("{not json")
        monkeypatch.setenv("AUTODEV_REPO_PATH", str(tmp_path))
        config = mcp.resolve_dashboard_config()
        assert config["base_url"] == "http://127.0.0.1:18790"


# ---------------------------------------------------------------------------
# Real HTTP round-trip (in-process stdlib server)
# ---------------------------------------------------------------------------

class _RecordingHandler(BaseHTTPRequestHandler):
    seen = []

    def _respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).seen.append({
            "method": "GET", "path": self.path,
            "auth": self.headers.get("Authorization"),
        })
        self._respond(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).seen.append({
            "method": "POST", "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": json.loads(raw) if raw else None,
        })
        self._respond(409, {"detail": "busy"})

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def live_dashboard():
    _RecordingHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestHttpRoundTrip:
    def test_bearer_token_and_query(self, live_dashboard):
        config = {"base_url": live_dashboard, "token": "sekrit", "timeout": 5.0}
        result = mcp.call_tool("pipeline_events", {"limit": 3}, config)
        assert not result["isError"]
        request = _RecordingHandler.seen[-1]
        assert request["path"] == "/api/events?limit=3"
        assert request["auth"] == "Bearer sekrit"

    def test_no_token_sends_no_auth_header(self, live_dashboard):
        config = {"base_url": live_dashboard, "token": "", "timeout": 5.0}
        mcp.call_tool("pipeline_state", {}, config)
        assert _RecordingHandler.seen[-1]["auth"] is None

    def test_post_body_and_409_mapping(self, live_dashboard):
        config = {"base_url": live_dashboard, "token": "sekrit", "timeout": 5.0}
        result = mcp.call_tool("answer_escalation", {"command": "RETRY"}, config)
        assert result["isError"]  # the fake server answers 409
        request = _RecordingHandler.seen[-1]
        assert request["method"] == "POST"
        assert request["path"] == "/api/command"
        assert request["body"] == {"command": "RETRY"}
        payload = json.loads(result["content"][0]["text"])
        assert payload["status"] == 409


# ---------------------------------------------------------------------------
# Stdio loop end-to-end (subprocess)
# ---------------------------------------------------------------------------

class TestStdioLoop:
    def _run(self, argv, messages):
        joined = "".join(json.dumps(m) + "\n" for m in messages)
        proc = subprocess.Popen(
            argv, cwd=REPO_ROOT, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(input=joined, timeout=60)
        assert proc.returncode == 0, err
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def test_module_invocation_handshake(self):
        responses = self._run(
            [sys.executable, "-m", "autodev.mcp.dashboard_server"],
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ],
        )
        assert len(responses) == 2  # the notification produces no response
        assert responses[0]["result"]["serverInfo"]["name"] == "lullabeast-dashboard"
        names = {t["name"] for t in responses[1]["result"]["tools"]}
        assert "pipeline_state" in names

    def test_direct_script_invocation(self):
        script = os.path.join(REPO_ROOT, "autodev", "mcp", "dashboard_server.py")
        responses = self._run(
            [sys.executable, script],
            [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}],
        )
        assert responses[0]["result"]["serverInfo"]["name"] == "lullabeast-dashboard"

    def test_parse_error_response(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "autodev.mcp.dashboard_server"],
            cwd=REPO_ROOT, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, _ = proc.communicate(input="this is not json\n", timeout=60)
        response = json.loads(out.splitlines()[0])
        assert response["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Client-registration example file
# ---------------------------------------------------------------------------

class TestExampleRegistration:
    def test_example_mcp_json_parses_and_targets_module(self):
        path = os.path.join(REPO_ROOT, ".mcp.json.example")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entry = data["mcpServers"]["lullabeast-dashboard"]
        assert "autodev.mcp.dashboard_server" in " ".join(entry["args"])

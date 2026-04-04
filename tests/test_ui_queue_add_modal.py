"""Tests for AddProjectModal repository path UX in ui/index.html."""
import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r") as f:
        return f.read()


def extract_function(html, func_name):
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{",
        html,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]

    next_fn = re.search(rf"\n\n{re.escape(indent)}function \w", remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        script_end = re.search(r"\n\s*</script>", remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder

    last_close = candidate.rfind(f"\n{indent}}}")
    if last_close != -1:
        return candidate[: last_close]
    return candidate


class TestAddProjectModalPath:
    def test_AddProjectModal_uses_ServerPathInput(self):
        body = extract_function(load_html(), "AddProjectModal")
        assert body is not None
        assert "ServerPathInput" in body

    def test_queue_modal_has_debounce_state(self):
        body = extract_function(load_html(), "AddProjectModal")
        assert body is not None
        assert "queueAddPathFsStatus" in body
        assert "setTimeout" in body

    def test_queue_modal_fetches_recents_on_mount(self):
        body = extract_function(load_html(), "AddProjectModal")
        assert body is not None
        assert "/api/setup/recent-projects" in body
        assert "useEffect" in body

    def test_queue_modal_no_create_folder(self):
        body = extract_function(load_html(), "AddProjectModal")
        assert body is not None
        assert "create-repo-dir" not in body

    def test_queue_modal_no_plus_indicator(self):
        body = extract_function(load_html(), "AddProjectModal")
        assert body is not None
        assert 'repoPathFsStatus === "parent"' not in body
        assert "text-amber" not in body

    def test_queue_modal_path_id(self):
        body = extract_function(load_html(), "AddProjectModal")
        assert body is not None
        assert 'id="queue-add-path"' in body

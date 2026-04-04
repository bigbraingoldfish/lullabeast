"""Tests for Switch project modal path UX in ui/index.html."""


def load_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def _switch_modal_chunk(html):
    """Slice around the Switch project dialog (excludes unrelated selects elsewhere)."""
    idx = html.find('id="switch-project-title"')
    assert idx != -1, "switch-project-title not found"
    return html[idx : idx + 9000]


class TestSwitchProjectModalPath:
    def test_switch_modal_uses_ServerPathInput(self):
        chunk = _switch_modal_chunk(load_html())
        assert "ServerPathInput" in chunk

    def test_switch_modal_has_switch_path_id(self):
        chunk = _switch_modal_chunk(load_html())
        assert 'id="switch-path"' in chunk

    def test_switch_modal_no_separate_recents_select(self):
        chunk = _switch_modal_chunk(load_html())
        assert "<select" not in chunk

    def test_switch_modal_has_debounce_state(self):
        html = load_html()
        assert "swFsStatus" in html
        assert "swValidateTimerRef" in html

    def test_switch_modal_no_plus_indicator(self):
        chunk = _switch_modal_chunk(load_html())
        assert 'repoPathFsStatus === "parent"' not in chunk

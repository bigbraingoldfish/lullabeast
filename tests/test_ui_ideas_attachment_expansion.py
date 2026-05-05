"""Tests for expanded file attachment support in IdeasScreen chat composer."""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


class TestAttachmentAcceptAttribute:
    """File input accept attribute covers images and text file types."""

    def _get_accept_value(self, content):
        m = re.search(
            r"ref=\{attachFileRef\}[\s\S]{0,300}accept=[\"']([^\"']+)[\"']",
            content,
        )
        assert m, "attachFileRef input element with accept attribute not found"
        return m.group(1)

    def test_accept_includes_image_extensions(self):
        content = load_index_html()
        accept_val = self._get_accept_value(content)
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
            assert ext in accept_val, f"'{ext}' missing from accept attribute, got: {accept_val}"

    def test_accept_retains_text_extensions(self):
        content = load_index_html()
        accept_val = self._get_accept_value(content)
        for ext in [
            # Markup/Docs
            ".md", ".txt", ".rst",
            # Code
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cpp", ".c", ".sh",
            # Config/Data
            ".json", ".yaml", ".yml", ".toml", ".xml", ".csv", ".sql",
        ]:
            assert ext in accept_val, f"'{ext}' missing from accept attribute, got: {accept_val}"


class TestFileReaderBranching:
    """FileReader method branches on file type: images use readAsDataURL, text uses readAsText.
    The shared processAttachmentFile function carries this logic so it works for both the
    file-picker button and the drag-and-drop drop handler."""

    def _get_handler_window(self, content):
        """Return a window of text starting at the processAttachmentFile definition."""
        m = re.search(r"(const|function)\s+processAttachmentFile\b", content)
        assert m, "processAttachmentFile function not found"
        idx = m.start()
        return content[idx: idx + 1500]

    def test_handler_calls_read_as_data_url_for_images(self):
        content = load_index_html()
        handler = self._get_handler_window(content)
        assert "readAsDataURL" in handler, \
            "readAsDataURL not found in processAttachmentFile"

    def test_handler_calls_read_as_text_for_text_files(self):
        content = load_index_html()
        handler = self._get_handler_window(content)
        assert "readAsText" in handler, "readAsText not found in processAttachmentFile"
        assert "readAsDataURL" in handler, "readAsDataURL not found in processAttachmentFile"

    def test_handler_checks_file_size(self):
        content = load_index_html()
        handler = self._get_handler_window(content)
        assert "f.size" in handler or "file.size" in handler, \
            "Expected client-side file size check (f.size or file.size) in handler"
        assert any(s in handler for s in ["5 * 1024 * 1024", "5242880", "5000000", "MAX_ATTACH"]), \
            "Expected 5 MB limit constant in processAttachmentFile"


class TestStagedAttachmentState:
    """stagedAttachment state object carries is_image flag."""

    def test_is_image_true_set_for_images(self):
        content = load_index_html()
        assert re.search(
            r"setStagedAttachment\s*\(\s*\{[^}]*is_image\s*:\s*true",
            content,
        ), "setStagedAttachment for image branch must include is_image: true"

    def test_is_image_false_set_for_text(self):
        content = load_index_html()
        assert re.search(
            r"setStagedAttachment\s*\(\s*\{[^}]*is_image\s*:\s*false",
            content,
        ), "setStagedAttachment for text branch must include is_image: false"


class TestAttachmentPillRendering:
    """Staged attachment pill shows img thumbnail for images, SVG icon for text."""

    def test_pill_shows_img_tag_for_images(self):
        content = load_index_html()
        idx = content.find("stagedAttachment.filename")
        assert idx != -1, "stagedAttachment.filename reference not found in pill"
        window = content[max(0, idx - 900): idx + 200]
        assert "is_image" in window, "Expected is_image conditional in attachment pill rendering"
        assert "<img" in window, "Expected <img> thumbnail element in attachment pill for image files"

    def test_button_tooltip_updated(self):
        content = load_index_html()
        assert 'title="Attach a .md file to this message"' not in content, \
            "Attach button tooltip should be updated from '.md file' to generic 'file'"
        assert re.search(r'title="Attach a file[^"]*"', content), \
            "Attach button should have updated generic tooltip"


class TestThreadDisplayPill:
    """Thread display pill for sent attachments handles image filenames."""

    def test_thread_pill_differentiates_image_by_extension(self):
        content = load_index_html()
        # Anchor to the specific filename span in the thread display pill (more specific than sent_context.attachment)
        idx = content.find("msg.sent_context.attachment.filename")
        assert idx != -1, "msg.sent_context.attachment.filename reference not found in thread display"
        window = content[max(0, idx - 400): idx + 400]
        has_img_logic = (
            ".png" in window
            or "isImgAtt" in window
            or bool(re.search(r"\.(jpg|jpeg|gif|webp)", window))
        )
        assert has_img_logic, \
            "Thread display pill should differentiate image attachments by extension or isImgAtt"


class TestDragAndDropAttachment:
    """Composer area supports drag-and-drop file attachment."""

    def test_drag_drop_handlers_present(self):
        content = load_index_html()
        # The composer area must declare onDrop, onDragOver, and onDragLeave handlers
        # so files dropped onto it stage as attachments without clicking the button.
        for handler in ("onDrop", "onDragOver", "onDragLeave"):
            assert handler in content, f"Composer should declare {handler} handler"

    def test_shared_attachment_file_handler_extracted(self):
        content = load_index_html()
        # A named function should be used to process files from BOTH the file input
        # and a drop event, avoiding duplicated FileReader logic.
        assert re.search(
            r"(const|function)\s+processAttachmentFile\b",
            content,
        ), "Expected a shared processAttachmentFile function to handle file-input and drop"

    def test_drag_state_tracked(self):
        content = load_index_html()
        # A boolean state variable indicates whether a drag is currently over the composer
        # so we can show a visual highlight.
        assert re.search(
            r"useState\([^)]*\)\s*[;,]?\s*//\s*drag|isDraggingOver|setIsDraggingOver|setDragHover|dragHover",
            content,
        ), "Expected a drag-hover state variable to drive visual feedback"

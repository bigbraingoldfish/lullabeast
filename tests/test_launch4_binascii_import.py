"""LAUNCH-4 — regression guard for the latent NameError the new ruff job surfaced.

``ui/server.py`` referenced ``binascii.Error`` in an image-attachment ``except``
clause (``except (ValueError, OSError, binascii.Error):``) without importing
``binascii``. That error path would therefore raise ``NameError`` (HTTP 500)
instead of the intended HTTP 422 whenever an invalid image attachment hit it —
a real bug ruff's F821 (undefined-name) caught the moment lint was added to CI.
"""
import binascii

import ui.server


def test_server_module_defines_binascii():
    # The name must resolve in the server module's namespace, else the
    # `except (ValueError, OSError, binascii.Error):` clause raises NameError
    # when evaluated (masking the intended HTTPException(422)).
    assert hasattr(ui.server, "binascii"), "ui/server.py must import binascii"
    assert ui.server.binascii is binascii

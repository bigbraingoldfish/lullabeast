"""Canonical atomic file writer for the Lullabeast pipeline (LAUNCH-5).

Single source of truth for the repo-wide "write a file atomically" idiom: render
the payload, write it to a **unique** temp file in the destination's own directory
(``tempfile.mkstemp``), then ``os.replace`` it over the destination. ``os.replace``
is an atomic rename on POSIX, so a reader never observes a half-written file and a
crash mid-write leaves the previous version intact. The temp is always removed if
the write fails, so a failure never strands a partial ``*.tmp`` next to the target.

Before this module the idiom was hand-inlined ~37 times under several different
names (``_atomic_write_json`` / ``_atomic_write_json_file`` / ``_write_json_atomic``
/ …). Three ``ui/server.py`` sites even used a *fixed* ``<path>.tmp`` suffix with no
cleanup — two concurrent writers would collide on that single name and leave corrupt
JSON, the exact hazard the unique-``mkstemp`` approach exists to avoid.

Two entry points, mirroring the two payload shapes in the codebase::

    write_json_atomic(path, data, *, indent=2, raise_on_error=True, fsync=False, encoding="utf-8")
    write_text_atomic(path, content, *, raise_on_error=True, fsync=False, encoding="utf-8")

Both accept ``str`` or ``Path`` and return ``True`` on success.

``raise_on_error`` preserves the divergent error policies of the call sites this
consolidated (LAUNCH-0 spike 2):

  * ``True`` (default) — re-raise on failure, for callers that must know a write
    failed (pipeline state, queue, session store).
  * ``False`` — swallow and return ``False``; the caller decides whether to log.
    Sites that previously *printed* on failure keep their exact log line via
    ``if not write_json_atomic(...): print("[ERROR] …")``.

``fsync`` defaults to ``False`` to preserve historical behavior (no prior site
fsync'd before replacing). Pass ``True`` to flush the file to disk before the rename
when durability against power loss matters more than write latency.

``json.dumps`` keeps its ``ensure_ascii=True`` default, so JSON output is
byte-identical to the prior ``json.dump(data, f, indent=2)`` call form (DEC-D).

NOT a candidate for this helper: ``event_log.append_pipeline_event`` uses O_APPEND
(append, not atomic-replace) — a different primitive, deliberately left alone.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


def _write_atomic(path, render: Callable[[], str], *,
                  raise_on_error: bool, fsync: bool, encoding: str) -> bool:
    """Write ``render()`` to a unique temp in *path*'s dir, then atomically replace.

    Shared core of :func:`write_json_atomic` / :func:`write_text_atomic`. ``render``
    is called *inside* the try block so a serialization error (e.g. a non-JSON value)
    is handled by the same cleanup path as an I/O error: the temp is removed and the
    destination is left untouched.
    """
    path = Path(path)
    directory = str(path.parent) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(render())
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, str(path))
        return True
    except BaseException as exc:
        # Clean up the temp on ANY failure (matches session_cleanup's original
        # BaseException guard — a KeyboardInterrupt mid-write must not strand a
        # temp). But only *swallow* ordinary Exceptions when raise_on_error is
        # False; control-flow exceptions (KeyboardInterrupt/SystemExit) always
        # propagate so raise_on_error=False can never eat a Ctrl-C or sys.exit.
        try:
            os.remove(tmp)
        except OSError:
            pass
        if raise_on_error or not isinstance(exc, Exception):
            raise
        return False


def write_json_atomic(path, data: Any, *, indent: int | None = 2,
                      raise_on_error: bool = True, fsync: bool = False,
                      encoding: str = "utf-8") -> bool:
    """Atomically write ``data`` as JSON to ``path``. See the module docstring.

    Returns ``True`` on success. On failure: re-raises when ``raise_on_error`` is
    ``True`` (the default), otherwise returns ``False``. The temp file is always
    cleaned up and the destination is never left partially written.
    """
    return _write_atomic(
        path, lambda: json.dumps(data, indent=indent),
        raise_on_error=raise_on_error, fsync=fsync, encoding=encoding,
    )


def write_text_atomic(path, content: str, *, raise_on_error: bool = True,
                      fsync: bool = False, encoding: str = "utf-8") -> bool:
    """Atomically write text ``content`` to ``path``. See the module docstring.

    The text-mode sibling of :func:`write_json_atomic` with the same temp-uniqueness,
    cleanup-on-failure, and ``raise_on_error`` semantics.
    """
    return _write_atomic(
        path, lambda: content,
        raise_on_error=raise_on_error, fsync=fsync, encoding=encoding,
    )


__all__ = ["write_json_atomic", "write_text_atomic"]

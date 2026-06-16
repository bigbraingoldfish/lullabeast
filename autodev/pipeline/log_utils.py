"""Shared logging primitives for the Lullabeast entry points (orchestrator + UI server).

Before this module the two processes diverged: the orchestrator logged via ~340
``print("[TAG] …")`` calls plus a bespoke ``_ensure_stdout_logging`` shim, while the
UI server used two differently-configured named loggers and ~14 ad-hoc prints, with
no shared helper. This module is the single home for the three primitives both now
use, so "what does a log line look like / where does it go / how is the level set"
has one answer.

Three functions, no framework:

* :func:`configure_stream_logging` — idempotent handler attachment for the root or a
  named logger. Generalizes the orchestrator's old ``_ensure_stdout_logging`` (which
  is now a thin wrapper over it).
* :func:`tagged` — emit one ``[TAG] message`` line. The ``[TAG] message`` token is
  guaranteed contiguous so it stays byte-compatible (as a substring) with the legacy
  ``print("[TAG] …")`` / ``logging("[TAG] …")`` lines the capsys tests assert on.
* :func:`resolve_log_level` — map a level *string* to the ``logging`` constant,
  degrading garbage to a default instead of raising (mirrors ``load_config``'s
  numeric-coercion discipline).

Kept dependency-light on purpose (only ``logging`` / ``os`` / ``sys``) so both entry
points and the gate scripts can import it.
"""

from __future__ import annotations

import logging
import os
import sys

# The orchestrator's canonical stdout format (timestamp + level + message). Named
# loggers may pass their own ``fmt`` (e.g. the server's readiness logger).
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def resolve_log_level(value, default: int = logging.INFO) -> int:
    """Map a level *name* (``"DEBUG"`` … ``"CRITICAL"``, case-insensitive) to the
    ``logging`` integer constant; return ``default`` for ``None``/blank/unknown.

    Never raises — an operator typo (``UI_LOG_LEVEL=loud``) degrades to the default
    rather than crashing import, the same forgiving posture ``load_config`` takes for
    its numeric keys. Also accepts an already-resolved ``int`` unchanged.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return default  # None / bool / anything non-string → default, never raise
    name = value.strip().upper()
    if name in _LEVEL_NAMES:
        return getattr(logging, name)
    return default


def _has_stream_handler(logger: logging.Logger, stream, level: int) -> bool:
    """True if *logger* already has a (non-file) StreamHandler bound to *stream* at a
    level no stricter than *level* — the idempotency guard for repeated configure calls."""
    for h in logger.handlers:
        if (
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and getattr(h, "stream", None) is stream
            and h.level <= level
        ):
            return True
    return False


def _has_file_handler(logger: logging.Logger, path: str) -> bool:
    target = os.path.abspath(path)
    return any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", None) == target
        for h in logger.handlers
    )


def configure_stream_logging(
    name: str | None = None,
    level: int = logging.INFO,
    *,
    stream=None,
    fmt: str = DEFAULT_FORMAT,
    logfile: str | None = None,
    propagate: bool | None = None,
) -> logging.Logger:
    """Attach a stdout (or *stream*) handler to the root or a named logger, idempotently.

    ``name=None`` configures the **root** logger (the orchestrator's case): used so
    module-level ``logging.*`` from imported helpers lands on the same operator stream
    as the orchestrator's prints. A ``name`` configures that named logger (the server's
    ``autodev.readiness`` / ``autodev.ui`` case).

    * ``stream`` defaults to ``sys.stdout`` resolved **at call time**, so re-invoking
      after ``sys.stdout`` is swapped (pytest capsys) attaches a fresh handler bound to
      the new stream and log lines stay visible.
    * The stream handler is added only when no matching one already exists (idempotent —
      repeated calls don't stack handlers).
    * ``logfile`` optionally adds a ``FileHandler`` (best-effort: an ``OSError`` opening
      it is swallowed so file logging can never block startup). Gated to *level*, so the
      file no longer receives DEBUG by default.
    * ``propagate`` is applied only when not ``None`` (named loggers that must not double
      up through the root pass ``False``).

    For the root logger the level is only *raised* toward *level* when it was quieter
    (preserving the old ``_ensure_stdout_logging`` behavior, which never lowered an
    operator-tightened root); a named logger's level is set explicitly.

    Returns the configured logger.
    """
    logger = logging.getLogger(name)
    target_stream = stream if stream is not None else sys.stdout

    if not _has_stream_handler(logger, target_stream, level):
        handler = logging.StreamHandler(stream=target_stream)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(fmt))
        logger.addHandler(handler)

    if logfile and not _has_file_handler(logger, logfile):
        try:
            file_handler = logging.FileHandler(logfile)
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(fmt))
            logger.addHandler(file_handler)
        except OSError:
            pass  # best-effort: file logging is optional, never block startup

    if name is None:
        # Root: only raise the level if it was too quiet; never lower an operator's choice.
        if logger.level == logging.WARNING or logger.level > level:
            logger.setLevel(level)
    else:
        logger.setLevel(level)

    if propagate is not None:
        logger.propagate = propagate

    return logger


def tagged(tag: str, msg: str = "", *, level: int = logging.INFO, logger: logging.Logger | None = None) -> None:
    """Emit one ``[TAG] message`` log line via *logger* (default: the root logger).

    The ``[%s] %s`` template guarantees the ``[TAG] message`` token is contiguous, so a
    line rendered through any handler format still contains that exact substring — the
    contract the orchestrator's capsys tests depend on. ``msg`` may be empty (``[TAG]``).
    """
    (logger or logging.getLogger()).log(level, "[%s] %s", tag, msg)


def set_level(level: int, *names: str) -> None:
    """Set *level* on each named logger AND all of its handlers.

    A bare ``logger.setLevel`` is not enough to *change* the effective level after the
    handlers were created at a stricter level (e.g. at import, before config loads): a
    record that passes the logger's threshold is still filtered by the handler's. The UI
    server calls this from ``load_config`` so ``ui/config.json``'s ``log_level`` actually
    reaches the already-created ``autodev.readiness`` / ``autodev.ui`` loggers, and so the
    reported ``config["log_level"]`` always equals the level that is really active.
    """
    for name in names:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        for h in lg.handlers:
            h.setLevel(level)


__all__ = ["configure_stream_logging", "tagged", "resolve_log_level", "set_level", "DEFAULT_FORMAT"]

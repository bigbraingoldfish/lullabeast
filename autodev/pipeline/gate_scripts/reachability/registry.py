"""P1 Stage F — entry-command classifier and resolver registry."""

import shlex


_PYTHON_PREFIXES = ("python", "python3", "uvicorn", "gunicorn", "hypercorn", "flask")
_JS_TS_PREFIXES = ("node", "npm", "yarn", "pnpm", "vite", "tsx", "ts-node")
_TEST_RUNNER_PREFIXES = (
    "pytest", "unittest", "jest", "mocha", "vitest", "playwright", "cypress",
)


def _first_token(command):
    """Tokenise via shlex; return the first non-empty token or ``""``."""
    if not command:
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return tokens[0] if tokens else ""


def _strip_npx(command):
    """If the leading token is ``npx``, drop it. Returns the remaining
    command; bare ``npx`` returns ``""`` so the caller can fall through to
    ``"unsupported"``."""
    cmd = (command or "").strip()
    if not cmd:
        return cmd
    head = _first_token(cmd)
    if head != "npx":
        return cmd
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    return " ".join(tokens[1:])


def classify_command(command):
    """Return one of ``"python"`` / ``"js_ts"`` / ``"test_runner"`` /
    ``"unsupported"`` / ``"empty"``. ``npx`` is stripped before classification."""
    cmd = (command or "").strip()
    if not cmd:
        return "empty"
    cmd = _strip_npx(cmd)
    if not cmd:
        # Bare ``npx`` with nothing wrapped — can't analyse the wrapped binary
        # because there isn't one.
        return "unsupported"
    head = _first_token(cmd)
    if not head:
        return "empty"
    # python* covers `python`, `python3`, `python3.11`, etc.
    if head == "python" or head.startswith("python"):
        # Disambiguate against pytest etc., which we matched separately below.
        if head in _TEST_RUNNER_PREFIXES:
            return "test_runner"
        return "python"
    if head in _PYTHON_PREFIXES:
        return "python"
    if head in _JS_TS_PREFIXES:
        return "js_ts"
    if head in _TEST_RUNNER_PREFIXES:
        return "test_runner"
    return "unsupported"


def get_resolver(command, project_root):
    """Return a ``PythonResolver`` / ``JsTsResolver`` instance for analysable
    commands; ``None`` for test_runner / unsupported / empty so the gate
    helper emits the appropriate signal."""
    classification = classify_command(command)
    if classification == "python":
        from .python import PythonResolver
        return PythonResolver()
    if classification == "js_ts":
        from .js_ts import JsTsResolver
        return JsTsResolver()
    return None

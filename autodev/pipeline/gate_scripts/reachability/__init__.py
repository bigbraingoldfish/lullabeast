"""P1 Stage F — static reachability resolvers (advisory only).

Public surface used by the executor gate and (via the same imports) by the
pipeline-side tests:

* ``ReachabilityResult`` — dataclass returned by every resolver.
* ``Resolver`` — Protocol satisfied by ``PythonResolver`` and ``JsTsResolver``.
* ``classify_command`` — string → "python" | "js_ts" | "test_runner" | "unsupported" | "empty".
* ``get_resolver`` — string → Resolver | None.
* ``PythonResolver`` / ``JsTsResolver`` — direct exports so resolver-level
  tests can construct them without going through the registry.

The package is co-located with ``utils.py`` under ``gate_scripts/`` so the
executor gate's existing ``current_dir``-on-``sys.path`` mechanism imports it
without a second ``sys.path`` hack.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ReachabilityResult:
    """Static-walk result. ``reachable`` is the set of workspace-relative
    paths the resolver could reach from the entry; ``limitations`` are
    human-readable strings the gate helper surfaces as ``resolver_limitation``
    diagnostics; ``entry_resolved`` is the entry script (workspace-relative)
    or ``None`` when it couldn't be derived."""
    reachable: set = field(default_factory=set)
    limitations: list = field(default_factory=list)
    entry_resolved: str = None


class Resolver(Protocol):
    """Protocol satisfied by PythonResolver and JsTsResolver."""
    language: str

    def resolve(self, project_root: str, entry_point_command: str) -> ReachabilityResult: ...


from .registry import classify_command, get_resolver  # noqa: E402
from .python import PythonResolver  # noqa: E402
from .js_ts import JsTsResolver  # noqa: E402

__all__ = [
    "ReachabilityResult",
    "Resolver",
    "classify_command",
    "get_resolver",
    "PythonResolver",
    "JsTsResolver",
]

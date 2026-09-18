"""Refuse to build a distribution without the built dashboard frontend.

evaltrack/ui/static is a gitignored build artifact. Without this hook, a build
from a fresh clone (`uv build`, or `pip install git+...`) produces a wheel or an
sdist with no dashboard and reports no error. CI covers its own builds. This
hook covers from-source builds outside CI.
"""

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class RequireFrontendHook(BuildHookInterface[Any, Any]):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs (uv sync, dev and CI test jobs) run without a
        # built frontend on purpose.
        if version == "editable":
            return
        index = Path(self.root) / "evaltrack" / "ui" / "static" / "index.html"
        if not index.is_file():
            raise RuntimeError(
                "evaltrack/ui/static/index.html is missing: the dashboard frontend "
                "has not been built, so the resulting distribution would ship "
                "without the UI. Build it first with `just frontend_build` "
                "(or `cd frontend && npm install && npm run build`), then rebuild."
            )

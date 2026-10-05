"""Refuse to build a distribution without the built frontend: the dashboard and
the report page.

evaltrack/ui/static and evaltrack/report/static are gitignored build artifacts.
Without this hook, a build from a fresh clone (`uv build`, or `pip install git+...`) produces a wheel or an
sdist with no dashboard and reports no error. CI covers its own builds. This
hook covers from-source builds outside CI.
"""

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


# Not subscripted. hatchling 1.32.1 and 1.32.3 gave the base class a second type
# parameter and 1.32.4 took it back, and a build resolves whichever is newest.
class RequireFrontendHook(BuildHookInterface):  # pyright: ignore[reportMissingTypeArgument]
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs (uv sync, dev and CI test jobs) run without a
        # built frontend on purpose.
        if version == "editable":
            return
        # One build writes both, so a missing one means the frontend was built
        # before the other existed, or not at all.
        missing = [
            path
            for path in (
                "evaltrack/ui/static/index.html",
                "evaltrack/report/static/report.html",
            )
            if not (Path(self.root) / path).is_file()
        ]
        if missing:
            raise RuntimeError(
                f"{' and '.join(missing)} missing: the "
                "frontend has not been built, so the resulting distribution "
                "would ship without the dashboard or the report page. Build it "
                "first with `just frontend_build` (or `cd frontend && npm "
                "install && npm run build`), then rebuild."
            )

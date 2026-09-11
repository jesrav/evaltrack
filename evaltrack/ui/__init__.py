"""Dashboard UI over one or more mounted repositories. Internal. These names can
change without notice."""

from evaltrack.ui.app import create_app
from evaltrack.ui.models import MountedRepository

__all__ = ["MountedRepository", "create_app"]

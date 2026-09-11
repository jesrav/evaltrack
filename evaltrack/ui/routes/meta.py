"""What the dashboard reads once at startup: the project settings and the mounts."""

from collections.abc import Mapping

from fastapi import APIRouter

from evaltrack.config import RepositoryRole
from evaltrack.ui.models import ProjectConfig, RepositoryInfo


def _rank_role(role: RepositoryRole) -> int:
    return {"local": 0, "remote": 1}[role]


def build_meta_router(
    project_config: ProjectConfig, infos: Mapping[str, RepositoryInfo]
) -> APIRouter:
    """Neither route reaches storage."""
    router = APIRouter(prefix="/api")

    @router.get("/config")
    def get_config() -> ProjectConfig:  # pyright: ignore[reportUnusedFunction]
        return project_config

    @router.get("/repositories")
    def list_repositories() -> list[RepositoryInfo]:  # pyright: ignore[reportUnusedFunction]
        return sorted(infos.values(), key=lambda i: (_rank_role(i.role), i.slug))

    return router

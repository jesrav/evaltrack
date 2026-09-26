"""Project configuration from the `[tool.evaltrack]` table in pyproject.toml."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    ValidationError,
    model_validator,
)

# `local` is where this project's own runs go. `remote` is the shared repository.
RepositoryRole = Literal["local", "remote"]


def _check_pr_url_template(value: str) -> str:
    if "{pr}" not in value:
        raise ValueError(
            "pr_url_template must contain the '{pr}' placeholder "
            "(e.g. 'https://github.com/OWNER/REPO/pull/{pr}')"
        )
    # `javascript:` and `data:` schemes execute when the link is followed.
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError(
            "pr_url_template must be an http(s) URL (got a non-http scheme)"
        )
    return value


# Every carrier of a template uses this type, so no value reaches a link target
# without the scheme check.
PrUrlTemplate = Annotated[str, AfterValidator(_check_pr_url_template)]

LOCAL_ENV = "EVALTRACK_LOCAL"
REMOTE_ENV = "EVALTRACK_REMOTE"
DEFAULT_LOCAL_PATH = "./.evaltrack"


class EvaltrackConfig(BaseModel):
    """The `[tool.evaltrack]` table. All fields optional. An unknown key is refused.

    Attributes:
        local: The working repository, a directory path or a repository URL.
            Unset means `./.evaltrack`. A relative path resolves against the
            pyproject.toml's directory.
        remote: The shared repository, a directory path or a repository URL.
            A relative path resolves as for `local`.
        pr_url_template: Link target for a PR ref, with a `{pr}` placeholder.
    """

    model_config = ConfigDict(extra="forbid")

    local: str | None = None
    remote: str | None = None
    pr_url_template: PrUrlTemplate | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_removed_keys(cls, data: object) -> object:
        # A removed key gets its own message, since "extra inputs are not
        # permitted" reads as a typo.
        if isinstance(data, dict) and "keep_raw_results" in data:
            raise ValueError(
                "keep_raw_results was removed in evaltrack 0.3.0. A run no "
                "longer stores the eval runner's own result objects. Remove "
                "the key. If your eval runner records traces, use them for the "
                "full data."
            )
        return data


def names_a_url(location: str) -> bool:
    """Whether `location` names a repository URL rather than a directory path,
    including one written without its `://`. A ':' in the first segment can only
    be a scheme, since a directory path must carry none there."""
    if "://" in location:
        return True
    scheme, colon, _ = location.partition(":")
    return bool(colon) and "/" not in scheme


def anchor_path(location: str, base: Path) -> str:
    """Rewrite a relative directory path to an absolute one under `base`. A URL,
    an absolute path and a `~` path come back unchanged.

    A URL written without its `://` is left alone too, so it stays recognisable
    as a URL instead of turning into a relative path with a ':' mid-way.
    """
    if names_a_url(location) or location.startswith("~") or os.path.isabs(location):
        return location
    return os.path.normpath(os.path.join(base, location))


def project_root(start: Path | None = None) -> Path:
    """The nearest directory at or above `start` (default cwd) holding a pyproject.toml,
    else `start` itself."""
    pyproject = _find_pyproject(start)
    if pyproject is not None:
        return pyproject.parent
    return (start or Path.cwd()).resolve()


def _find_pyproject(start: Path | None = None) -> Path | None:
    base = (start or Path.cwd()).resolve()
    for directory in (base, *base.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _describe_config_errors(exc: ValidationError) -> str:
    """Render the rejected keys without their values, which can carry a SAS token."""
    return "".join(
        f"\n  {'.'.join(str(part) for part in error['loc']) or '[tool.evaltrack]'}: "
        f"{error['msg']}"
        for error in exc.errors(include_input=False)
    )


def load_config(start: Path | None = None) -> EvaltrackConfig:
    """Load `[tool.evaltrack]` from the nearest pyproject.toml, empty when there is
    none.

    `local` and `remote` are a directory path or a repository URL. A relative
    path is anchored to the pyproject's directory.

    Raises:
        ValueError: naming the file when it is not valid TOML, or when the
            `[tool.evaltrack]` table holds an unknown key or an invalid value.
    """
    path = _find_pyproject(start)
    if path is None:
        return EvaltrackConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"could not parse {path}: {exc}") from exc
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        # `tool = 5` is valid TOML.
        raise ValueError(
            f"invalid pyproject.toml at {path}: top-level 'tool' must be a "
            f"table, got {type(tool).__name__}"
        )
    try:
        config = EvaltrackConfig.model_validate(tool.get("evaltrack", {}))
    except ValidationError as exc:
        # The file is found by searching upward, so the message must name it.
        raise ValueError(
            f"invalid [tool.evaltrack] in {path}: {_describe_config_errors(exc)}"
        ) from exc
    base = path.parent
    return config.model_copy(
        update={
            role: anchor_path(location, base) if location else None
            for role, location in (("local", config.local), ("remote", config.remote))
        }
    )


@dataclass(frozen=True)
class ConfiguredRemote:
    """A configured remote repository and the label naming where it came from."""

    url: str
    source: str


def resolve_local(
    config: EvaltrackConfig, *, override: str | None = None, start: Path | None = None
) -> str:
    """The local repository: `override`, then `$EVALTRACK_LOCAL`, then `config.local`,
    else `./.evaltrack` under the project at or above `start` (default cwd).

    An override and an environment value are used as written, so a relative path
    in either follows the working directory, as a typed one does.
    """
    if override:
        return override
    env = os.environ.get(LOCAL_ENV)
    if env:
        return env
    return config.local or anchor_path(DEFAULT_LOCAL_PATH, project_root(start))


def resolve_remote(config: EvaltrackConfig) -> ConfiguredRemote | None:
    """The remote repository: `$EVALTRACK_REMOTE`, then `config.remote`, else None.

    A remote is optional and its source is displayed, where a local one is
    neither, so this returns a different shape from `resolve_local`.
    """
    env = os.environ.get(REMOTE_ENV)
    if env:
        return ConfiguredRemote(env, f"${REMOTE_ENV}")
    if config.remote:
        return ConfiguredRemote(config.remote, "[tool.evaltrack].remote")
    return None

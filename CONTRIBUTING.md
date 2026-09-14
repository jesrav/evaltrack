# Contributing

Bug reports, feature ideas, and pull requests are welcome. For a large change, open an issue first.

## Development setup

Develop on Linux or macOS. Those are what evaltrack supports and what CI runs, and the suite does
not pass on Windows. Under WSL2 you are on Linux, so that works.

Install [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just). Then run:

```bash
just install_dev   # uv sync --all-groups --extra ui --extra azure + install pre-commit hooks
```

For the dashboard frontend (TypeScript/React):

```bash
just frontend_install
just frontend_dev      # Vite dev server, proxies /api to a running `evaltrack ui` (default :8765)
```

An editable install serves a fallback page at `evaltrack ui` until `just frontend_build` has run.

## Tests and linting

```bash
just test          # run the test suite
just lint          # ruff, basedpyright (strict), bandit, prettier (via pre-commit)
```

The justfile has the rest: the frontend checks, the runnable examples, and the live cloud
integration tests. Run what matches your change (for the frontend, also `npm run lint`, which
`just lint` does not cover). CI runs all of it on every PR bar the one exception below, so anything
you skip locally is still caught there.

The integration tests need credentials for the maintainer's Azure and AWS test accounts, so most
contributors cannot run them locally. That is fine. A PR from a fork is not given the secrets
either, so that job skips there rather than failing on empty credentials, and the run against live
storage happens after merge. If your change touches the Azure or S3 backend, say so in the PR and I
will run them. The azure and s3 fixtures in `tests/conftest.py` have the details.

Locally, the Azure tests use `az login`. The S3 tests use the AWS profile that `AWS_PROFILE` names.
Run `AWS_PROFILE=<profile> just integration_test`, or put `AWS_PROFILE=<profile>` in a `.env` file.
The justfile loads that file and git ignores it.

## Conventions

- Branch off `main`. A pre-commit hook blocks direct commits to `main`.
- Add tests for new behavior and for bug fixes.
- Prefer dependency injection with fakes over mocking. Test what a caller can observe, not internal
  behavior.
- If you change the stored run format, bump `RUN_SCHEMA_VERSION`. Its docstring in
  `evaltrack/core/run_record.py` says what else a bump needs.

## Releasing

A release is a pull request and then a GitHub release. The pull request bumps `version` in
`pyproject.toml`, runs `uv lock` so the lockfile carries the new version, and turns the changelog's
`[Unreleased]` heading into the version and date, with a link reference at the bottom. Once it is
merged, publish a GitHub release with the tag `v<version>` and the changelog section as its notes.
That runs `release.yml`, which refuses a tag that does not match the declared version, builds and
smoke-tests the distribution, and uploads it to PyPI through the `pypi` environment. Releasing needs
write access to the repository and approval on that environment.

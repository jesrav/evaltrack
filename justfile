set dotenv-load:= true

# Install dependencies and pre-commit hooks
install_dev:
    uv sync --all-groups
    uv run pre-commit install

# Lint code with pre-commit
lint:
    uv run pre-commit run --all-files

# Run tests (skips the live cloud integration tests, so no network or credentials needed).
# No positional target. pyproject's `testpaths` collects tests/ by default, and
# `just test tests/test_foo.py` narrows to that file instead of appending to the whole suite.
test ARGS="":
    uv run pytest -m "not integration" {{ARGS}}

# Run the examples. They are async tests, so an async plugin comes along just for
# this command instead of being a project dependency. Narrow to one file with
# `just examples examples/test_01_function.py`.
examples ARGS="examples/":
    uv run --with pytest-asyncio pytest {{ARGS}}

# Run the one test that needs pytest-asyncio on its own, with the loop scope the
# plugin wants. `just test` covers it too, since deepeval brings pytest-asyncio in.
asyncio_test:
    uv run --with pytest-asyncio pytest tests/plugin/test_async_plugins.py -o asyncio_default_fixture_loop_scope=function

# Run the live cloud integration tests. They need `az login` and an AWS profile, see CONTRIBUTING.md.
integration_test ARGS="":
    uv run pytest -m integration {{ARGS}}

# Frontend dashboard (TS/React/Vite). See /frontend
frontend_install:
    cd frontend && npm install

# Dev: starts Vite on :5173, proxying /api to a separately-running `evaltrack ui` on :8765
frontend_dev:
    cd frontend && npm run dev

# Build the frontend into evaltrack/ui/static so `evaltrack ui` serves it
frontend_build:
    cd frontend && npm run build

# Type-check the frontend without emitting a build
frontend_typecheck:
    cd frontend && npm run typecheck

# Run the frontend test suite (mirrors CI's frontend job)
frontend_test:
    cd frontend && npm run test

# Build the documentation site. Strict, so a broken link or an unresolved
# docstring cross-reference fails the build, as it does in CI.
docs_build:
    uv run zensical build --strict

# Serve the documentation site locally with live reload
docs_serve:
    uv run zensical serve

# Build the documentation site with MkDocs and mkdocs-material instead of
# Zensical, to prove mkdocs.yml still works as a fallback. The mike fork in the
# docs group registers no MkDocs plugin, so the original mike is brought in.
docs_fallback:
    uv run --with mkdocs-material --with mike==2.2.0 mkdocs build --strict --site-dir site-fallback

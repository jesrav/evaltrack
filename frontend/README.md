# evaltrack frontend

React + TypeScript + Vite dashboard for evaltrack.

**Status: beta.** AI coding agents wrote most of this frontend, the two delete actions (runs and
refs) and the server's Origin check on writes are the reviewed security surface, the pure logic has
unit tests (`src/*.test.ts`, run with `npm run test`), and the components are typechecked.

Source layout (main modules):

    src/
      App.tsx             top-level layout, fetches + state
      report.tsx          the static report page, rendered from data embedded in it
      reportData.ts       reads that embedded data
      api.ts              fetch wrappers over /api/*
      deferred.ts         reading the envelopes that stand in for large values
      drawerOpener.ts     opening a pane that needs values the run left out
      diff.ts             pure two-run diff computation
      types.ts            TS mirror of the pydantic models
      components/         Sidebar, RunDetail, RunDiff, and smaller widgets

## Run it

Start the FastAPI backend from a project that has recorded runs:

    uv run evaltrack ui

It mounts the repositories from that project's `[tool.evaltrack]`.

In another terminal, run the Vite dev server:

    just frontend_install   # one-time
    just frontend_dev

Open http://localhost:5173. Vite proxies `/api` to the backend on port 8765 (the `evaltrack ui`
default). The dev server has full data and hot-reload on every TS save.

## Build for use with `evaltrack ui`

    just frontend_build

This writes the bundle into `../evaltrack/ui/static/`. The FastAPI app then serves it at `/` (and
`/assets/*`) the next time you run `evaltrack ui`. The same build writes `report.html` beside it,
one self-contained file (`vite.report.config.ts`) that `evaltrack report` fills with a run.

Package builds (`uv build`) fail until this bundle exists (`hatch_build.py` at the repo root
enforces this). So a from-source install cannot ship without the dashboard. Editable installs
(`uv sync`) do not need the bundle.

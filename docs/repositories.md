# Repositories and storage

evaltrack stores two kinds of things. A **run** is an immutable record of the evals from one pytest
session, and a **ref** is a mutable name that points at a run. This page defines that model, then
covers where the data lives, how to grant access, and how to read runs back.

## Run

A run nests what one pytest session recorded:

```text
run          when it was recorded, the git commit, whether the worktree was dirty,
             and arbitrary labels (`branch`, `build_id`, `environment`, ...)
└─ test      keyed by pytest nodeid, with pytest's own outcome for it
             (`passed`, `failed`, `errored`, `skipped`, `xfailed`, `xpassed`)
   └─ case   keyed by case id, with the outcome the assertions and score bars
             reached (`passed`, `failed` or `errored`)
      └─ attempt   one try at that case, earliest first
```

A test records one eval, so its cases are that eval's cases.

Runs never change after creation. Anything that changes over time is expressed with refs.

Each run is identified by a **ULID**, a time-sortable id assigned at record time. The id is assigned
once and never changes.

## Ref

A **ref** is a mutable, named reference to a run, the way a git branch points at a commit. Runs are
keyed by generated ids, so a ref gives you a stable handle that you can move to a newer run over
time.

A ref keeps a **reflog**, the history of every run that it pointed at. Each move adds an entry with
the target run and, optionally, a commit, a PR number, and a PR title. Past moves are never lost.
The dashboard uses that history to compare a run against past PRs and releases.

A ref name is lowercase and can be `/`-separated, as in `pr/123` or `my-experiment`. `baseline` is
reserved.

A ref is treated as a pull request when its latest reflog entry carries a PR number (recorded by
`evaltrack push --pr`), not because of its name.

## Baseline and mainline

**`baseline` is the ref.** The **mainline is the history of runs that ref has pointed at.**

`baseline` points at the most recently promoted run. Its reflog records every promoted run before
that one, with its commit and time, and that history is the mainline: what reliability rates and
score histories are measured over. You decide what "promoted" means (merged, deployed, released).
See [Adapting the flow].

## Promote

`evaltrack promote pr/123 --commit <sha>` moves `baseline` onto the run that `pr/123` already points
at. Nothing is copied and nothing is re-evaluated. The recorded run, with the same ULID, becomes the
newest mainline run.

### Moving a ref is idempotent

A move onto the run a ref already points at records nothing. No reflog entry is appended, and the
commit, PR number and title passed with the move are dropped. A retried CI job is therefore safe.
The cost is that you cannot amend the entry at the tip. If you promote the same run again with a
different `--commit`, the first commit stays.

## Backends

A repository is named by a directory path, or by a URL whose scheme picks the backend:

| Name                        | Backend                              |
| --------------------------- | ------------------------------------ |
| `./.evaltrack`              | Local directory (default)            |
| `azure://account/container` | Azure Blob Storage (`[azure]` extra) |

How a relative path resolves is in
[Configuration › Repository names](./configuration.md#repository-names).

## Configuring repositories

Declare them once in the `[tool.evaltrack]` table of `pyproject.toml`: `local` is where the pytest
plugin saves, `remote` the shared one CI pushes to, holding the `baseline` and `pr/{n}` refs. Both
are optional, and `evaltrack ui` mounts whichever exist. See
[Configuration](./configuration.md#toolevaltrack-in-pyprojecttoml).

## Azure Blob Storage

> Requires the `[azure]` extra: `uv add "evaltrack[azure]"`.

A shared remote is an Azure Blob container, addressed as `azure://<account>/<container>[/<prefix>]`.
The optional prefix namespaces a container. Several projects can share one, for example
`azure://myevals/evals/project-a`.

A ref's reflog is an append blob, and every move of the ref appends one block. Azure allows at most
50,000 blocks per append blob, so `baseline` can take about 50,000 promotes before a move fails. The
repair is the same as for a [torn reflog](#repairing-a-torn-reflog).

### Authentication

evaltrack authenticates with Azure's `DefaultAzureCredential`, so there is no evaltrack-specific
secret to manage:

- **Developers** run `az login` once. The Azure CLI credential is picked up automatically.
- **CI** needs to set the service-principal variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
  `AZURE_CLIENT_SECRET`. The credential reads them with no extra login step.

Both need the Storage Blob Data Contributor role on the account.

## Cleaning up runs

A shared remote repository grows with every PR run, and merged or abandoned PRs leave behind
orphaned runs. Your local `.evaltrack/` also accumulates runs, but there you can just delete
individual runs or the whole directory.

A run survives while any ref reaches it, whether a ref points at it now or once did. Anything else
can be deleted, and three actions do the deleting:

- `promote --cleanup` (merged PRs): after moving `baseline` onto the PR's run, delete the merged
  `pr/{n}` ref and the runs in its history that no other ref reaches. The promoted run survives via
  the baseline reflog. Use it wherever CI promotes. See [CI/CD](./ci-cd.md#promote-pipeline).
- **Deleting a ref in the dashboard** (dead PRs): a PR closed without merging keeps its ref.
  Deleting it takes the runs its history reaches with it.
- **Deleting a single run in the dashboard** (stray runs): only a run that no ref reaches can go
  this way.

Deleting is the dashboard's one write feature, and `baseline` is protected, so the mainline survives
both.

The dashboard is an unauthenticated server bound to `127.0.0.1`. The
[security policy](../SECURITY.md#dashboard-trust-model) has the threat model.

### Repairing a torn reflog

A crash during a move can leave a torn last line in a ref's reflog. Reading that ref then fails, and
the error names the line. So does every delete in the repository, even one that targets another ref.
A delete has to know which runs are still referenced, and it cannot read this reflog to find out.
The dashboard shows the ref as `unreadable history`.

To repair it, write the readable lines back without the torn one, each ending in a newline. On
Azure, write them in one append to a new append blob, since a block blob refuses every later append.

## Reading runs from Python

Recorded runs are available in Python via `open_repository` / `load_run`. The CLI's
[`export`](./cli.md#evaltrack-export) command is a thin wrapper over the same calls.

```python
from evaltrack import open_repository

repo = open_repository("./.evaltrack")
run = repo.load_run("<run-id>")  # a RunRecord, or None if absent

# Structured per-case data: outcome, attempts, latency, errors. A test that
# never evaluated (it errored or skipped) has no case.
for nodeid, test in run.tests.items():
    for case_id, case in test.cases.items():
        attempt = case.attempts[0]
        print(case_id, case.outcome, attempt.outcome, attempt.task_duration)

# The eval runner's own result objects, one per round, when keep_raw_results
# is on. Plain JSON data, not the runner's own types (see
# docs/configuration.md#raw-results):
raw_results = run.tests["tests/test_x.py::test_x"].raw_results
```

---

**Related:** [CI/CD](./ci-cd.md) · [CLI](./cli.md)

[adapting the flow]: ./ci-cd.md#adapting-the-flow-to-your-delivery-process

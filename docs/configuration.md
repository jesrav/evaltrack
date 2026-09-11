# Configuration reference

All configuration in one place: the marker, `[tool.evaltrack]` in `pyproject.toml`, pytest options,
and environment variables.

## Marker keyword arguments

```python
@pytest.mark.evaltrack(score_bars={"helpfulness": 0.8}, flake_reruns=2,
                       reliability_target=0.9, eval_version="prompt-v3")
def test_my_eval() -> None: ...
```

| kwarg                | default | meaning                                                                              |
| -------------------- | ------- | ------------------------------------------------------------------------------------ |
| `score_bars`         | `{}`    | per-case score bars, keyed by score name (see [the marker](./marker.md#score-bars))  |
| `repeats`            | `1`     | every case needs N attempts and all must pass, refused together with `flake_reruns`  |
| `flake_reruns`       | `0`     | rerun the eval while a case is failing, up to N extra rounds, stopping once all pass |
| `reliability_target` | `None`  | cross-run pass-rate target in `(0, 1]`, shown in the dashboard (never fails a test)  |
| `eval_version`       | `None`  | version label for the agent or prompt under test, segments the reliability history   |

All kwargs are keyword-only and checked at collection time. evaltrack checks every marker that
applies to a test, including markers inherited from its module or class.

## `[tool.evaltrack]` in pyproject.toml

evaltrack reads the table from the first `pyproject.toml` at or above the directory it starts from.
All keys are optional.

```toml
[tool.evaltrack]
local  = "./.evaltrack"            # where the pytest plugin saves runs
remote = "azure://account/evals"   # the shared source of truth (baseline, PRs)
keep_raw_results = false           # true to also store the eval runner's own result objects
# Where a PR ref links to. Must start with http:// or https:// and contain {pr}.
# GitHub:
pr_url_template = "https://github.com/OWNER/REPO/pull/{pr}"
# Azure DevOps:
# pr_url_template = "https://dev.azure.com/ORG/PROJECT/_git/REPO/pullrequest/{pr}"
```

| key                | default        | meaning                                                                                      |
| ------------------ | -------------- | -------------------------------------------------------------------------------------------- |
| `local`            | `./.evaltrack` | the plugin's save target and the dashboard's **Local** mount                                 |
| `remote`           | unset          | the shared repository: default target for `push`/`promote`, the dashboard's **Remote** mount |
| `keep_raw_results` | `false`        | keep the eval runner's own result objects on each run (see [Raw results](#raw-results))      |
| `pr_url_template`  | unset          | `http(s)` URL template with a `{pr}` placeholder, used as a PR ref's link target             |

### Raw results

With `keep_raw_results = true`, a test's eval also stores the eval runner's own result objects, one
per round, alongside the structured per-case data. You can then [export](./cli.md#evaltrack-export)
them or feed them back into that runner's own tooling.

## Pytest options

| option                                 | kind     | meaning                                                                                      |
| -------------------------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `--evaltrack-repository <path-or-url>` | CLI flag | save the run to this repository (overrides `[tool.evaltrack].local`)                         |
| `--evaltrack-run-file <file>`          | CLI flag | write the run as JSON instead of saving to a repository (used in CI before `evaltrack push`) |

`--evaltrack-repository` and `--evaltrack-run-file` are mutually exclusive.

Missing parent directories are fine. They are created when the run is written.

## Environment variables

| variable                | read by                | meaning                                                                                                           |
| ----------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `EVALTRACK_LOCAL`       | the CLI and the plugin | the local repository. The plugin saves to it, `--local` uses it, and `ui` mounts it                               |
| `EVALTRACK_REMOTE`      | the CLI                | the remote repository. `--remote` uses it, `push` / `promote` fall back to it, and `ui` mounts it                 |
| `EVALTRACK_COMMIT`      | run context            | authoritative commit SHA, replaces git detection. The git-derived `branch` label and `worktree_dirty` are dropped |
| `EVALTRACK_BRANCH`      | run context            | recorded as the `branch` label                                                                                    |
| `EVALTRACK_LABEL_<KEY>` | run context            | arbitrary label, lowercased key (for example `EVALTRACK_LABEL_BUILD_ID=42`)                                       |

The Azure backend also reads the standard `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
`AZURE_CLIENT_SECRET` variables via `DefaultAzureCredential`. See
[Repositories › Authentication](./repositories.md#authentication).

## Precedence rules

### Where the pytest plugin saves the run

Defaults to `local`.

1. `--evaltrack-repository <path-or-url>`
2. `EVALTRACK_LOCAL`
3. `[tool.evaltrack].local`
4. `./.evaltrack`

### What the dashboard mounts

The local repository, plus the remote when one is configured, resolved exactly as `--local` and
`--remote` resolve them. `evaltrack ui` takes no repository flags, so `EVALTRACK_LOCAL` and
`EVALTRACK_REMOTE` are the only way to redirect it without editing `pyproject.toml`. See
[CLI › evaltrack ui](./cli.md#evaltrack-ui).

## Repository names

A repository is named by a directory path, or by an `azure://account/container[/prefix]` URL
([Backends](./repositories.md#backends)).

Where a relative path resolves from depends on where you wrote it:

| written in                        | resolves against                                                  |
| --------------------------------- | ----------------------------------------------------------------- |
| `pyproject.toml`                  | the directory holding it, so every tool finds the same repository |
| an environment variable or a flag | the working directory                                             |

---

**Related:** [CLI](./cli.md) · [The evaltrack marker](./marker.md) ·
[Repositories and storage](./repositories.md)

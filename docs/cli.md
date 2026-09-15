# CLI reference

`evaltrack --help` lists the commands and `evaltrack <command> --help` documents every flag. This
page covers what the help output cannot: what a command's output means, and what the exit codes
promise.

## evaltrack push

Re-pushing under an existing run id replaces the stored run, so re-pushing the same file is safe.
The ref is separate. A re-push does not update the `--commit`, `--pr` or `--title` on a ref that
already points at the run. See
[Moving a ref is idempotent](./repositories.md#moving-a-ref-is-idempotent).

## evaltrack promote

See [Repositories › Promote](./repositories.md#promote) and
[Cleaning up runs](./repositories.md#cleaning-up-runs).

## evaltrack runs

Lists recorded runs, newest first, one per line: the run id, the recording time in UTC, the short
commit, the test counts, and the `branch` label when the run recorded one. A commit carries `-dirty`
when the working tree had uncommitted changes at eval time.

```text
$ evaltrack runs --local
01KZ5HPEFPMGTMCXB288BVGECV  2026-08-04T04:49:46Z  9b8c7d6e-dirty  2/14 failed  (feature/better-prompt)
01KZ5HPEFMEBFBF39RT2SAJSJV  2026-08-04T04:49:46Z  3f9c1a2b        12 tests, none failed  (main)
```

## evaltrack refs

Lists refs and the runs they point at. [`baseline`][baseline] comes first, then the PR refs, then
the rest. A line holds the ref name, the run it targets, when it last moved (UTC), and the PR number
and title when the move recorded them.

```text
$ evaltrack refs --remote
baseline  01KZ5HPEFMEBFBF39RT2SAJSJV  2026-08-04T04:49:47Z
pr/482    01KZ5HPEFPMGTMCXB288BVGECV  2026-08-04T04:49:47Z  #482 Rework the summarizer prompt
```

Both listings are for reading. The fields and their order can change in any release. Don't script
against them.

## evaltrack export

Prints a recorded run as JSON. Use it to inspect a run, archive one outside the repository, or feed
the eval runner's own result objects back into that runner's tooling when the run was recorded with
`keep_raw_results = true`. See [Raw results](./configuration.md#raw-results). The same data is
available in Python — see [Reading runs from Python].

**Exit codes:** `0` when the run was exported. `1` when the named repository does not hold the run,
and the error names that repository. `2` when no repository is named. No other command exits
[`1`](#exit-codes), so a script can act on the missing run alone.

## evaltrack ui

The remote is not contacted at startup, so the dashboard comes up at once even when the remote is
out of reach, for example without network or with an expired login. Its runs and refs then fail as
you browse them, and the dashboard shows the storage error while your local runs stay usable.

The server always listens on your own machine, and no flag changes that. The [dashboard trust model]
has why. The command hands the terminal to the server, so a server that cannot start exits with the
server's own code rather than the codes below.

## Exit codes

The four codes mean the same thing for every command. Where a command has more to say about one, it
carries an **Exit codes** note.

| Code | Meaning                                                                |
| ---- | ---------------------------------------------------------------------- |
| `0`  | The command did what you asked                                         |
| `1`  | A defined outcome that is not success, documented per command          |
| `2`  | You or the environment has something to fix, and the message says what |
| `70` | A bug in evaltrack, printed with its traceback                         |

Only [`export`](#evaltrack-export) exits `1` today, for a run the repository does not hold. Please
[report a `70`](https://github.com/jesrav/evaltrack/issues) with the traceback.

---

**Related:** [CI/CD](./ci-cd.md) · [Repositories and storage](./repositories.md)

[baseline]: ./repositories.md#baseline-and-mainline
[reading runs from python]: ./repositories.md#reading-runs-from-python
[dashboard trust model]:
  https://github.com/jesrav/evaltrack/blob/main/SECURITY.md#dashboard-trust-model

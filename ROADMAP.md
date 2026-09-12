# Roadmap

This is directional and priorities and scope will change.

## More storage remotes

- S3 and Google Cloud Storage, alongside the existing Azure Blob remote.

## Sharing results

- A single-file HTML report of a run, or of a run against the baseline, that opens anywhere with no
  server. Meant for CI artifacts and release notes.
- A Markdown summary of a run against the baseline, for posting on a pull request.

## CI integration

- A GitHub Action that wraps the push and promote flow.

## Running evals

- pytest-xdist support, to run evals in parallel across workers.

## Dashboard

- The pooling window as a project setting and as a control in the dashboard.

- Runner details on every attempt, and link templates that turn a recorded trace id into a link to
  an observability platform.

- Images in inputs and outputs, stored once by content hash and rendered inline.

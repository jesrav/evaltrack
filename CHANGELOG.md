# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog], and this project adheres to [Semantic Versioning].

[keep a changelog]: https://keepachangelog.com/en/1.1.0/
[semantic versioning]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

### Added

- `evaltrack report`, which writes a recorded run, or a run against the baseline, as one HTML file
  with the run embedded. It shows the run as the dashboard does and opens anywhere with no server
  and no network, for CI artifacts and release notes. A value over 16 KB is left out of the page
  unless `--full` is passed, so a report of a large run stays small. See
  [CLI › report](https://evaltrack.jesravnbol.dk/latest/cli/#evaltrack-report).
- **Report** in the dashboard, on a run and on a comparison, which saves the same page for the run
  on screen, to share with someone without the dashboard.

### Changed

- The mainline is the configured `remote` and nothing else. A dashboard with only a local repository
  mounted no longer measures reliability history over the local `baseline`. Point `remote` at a
  second local directory to keep that history when working alone.

## [0.3.0] - 2026-09-26

### Added

- The documentation site at [evaltrack.jesravnbol.dk](https://evaltrack.jesravnbol.dk), versioned by
  minor release, with an API reference generated from the docstrings.
- `RunRepository`, `RunSummary` and `ReflogEntry` are exported from the package root. They are what
  `open_repository()`, `list_runs()` and `get_ref()` hand back, so a reader can now name every type
  on the repository read path.
- At the end of a pytest session, evaltrack reports each attempt whose stored output is larger than
  256 KB. The report names the test and the case, and says what to do. See
  [Stored outputs › Large outputs](https://evaltrack.jesravnbol.dk/latest/outputs/#large-outputs).
- `evaltrack.converters.register` registers a converter, which changes how evaltrack stores the
  values of one type, for example to keep the tool calls of an agent run. See
  [Stored outputs › Converters](https://evaltrack.jesravnbol.dk/latest/outputs/#converters).
- The dashboard stays responsive on a large run. It sends each value over 16 KB to the browser as a
  preview with its size and a hash, and sends the whole value when a pane needs it. This applies to
  inputs, expected outputs, metadata and attempt outputs. Two runs still diff. A large value is
  compared by a hash of the same part that is compared for a small value. The server still reads the
  whole stored run. It keeps the last few runs that it read, so the panes of an open run do not read
  the run again.
- While a run loads, the dashboard shows how much of it has arrived. The run list shows the stored
  size of each run, which the summary sidecar now records.

### Changed

- **Breaking:** evaltrack stores a dataclass without its private fields, the fields whose name
  starts with `_`. A library often keeps its internal state in such fields, for example the message
  history of an agent framework. When a task returned such an object, a run grew to hundreds of
  megabytes. See
  [Stored outputs › Private fields](https://evaltrack.jesravnbol.dk/latest/outputs/#private-fields).
- evaltrack writes a run as compact JSON, not indented. The saving is largest for deeply nested
  values. The run that prompted this change went from 221 MB to 79 MB. A run of mostly long text
  saves much less. evaltrack still reads indented runs, so the run schema version does not change.
  `evaltrack export` prints the compact form.

### Removed

- **Breaking:** the `keep_raw_results` setting. Remove it from `[tool.evaltrack]`, or evaltrack
  stops with an error that says so. A run no longer stores the eval runner's own result objects, and
  a recorded test has no `raw_results` field. These objects grew with every output, and a loaded run
  cannot give them back to the runner. If your eval runner records traces, use them for the full
  data. Runs recorded with raw results still load, and the download and `export` include them. The
  dashboard does not show them.

## [0.2.0] - 2026-09-15

### Added

- Amazon S3 as a remote repository backend, addressed as `s3://bucket[/prefix]` and installed with
  the `[s3]` extra. Authentication uses boto3's default credential chain. See
  [Repositories › Amazon S3](https://evaltrack.jesravnbol.dk/latest/repositories/#amazon-s3).

## [0.1.0] - 2026-09-11

The first public release. evaltrack is a pytest plugin that records, gates and tracks LLM evals. See
the [README](https://github.com/jesrav/evaltrack#readme) to get started.

[unreleased]: https://github.com/jesrav/evaltrack/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.3.0
[0.2.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.2.0
[0.1.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.1.0

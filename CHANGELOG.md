# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog], and this project adheres to [Semantic Versioning].

[keep a changelog]: https://keepachangelog.com/en/1.1.0/
[semantic versioning]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

### Added

- Databricks Unity Catalog volumes as a remote repository backend, addressed as
  `databricks://catalog/schema/volume[/prefix]` and installed with the `[databricks]` extra. It goes
  through the Files API, so no cluster is needed, and authenticates with Databricks unified
  authentication. See
  [Repositories › Databricks volume](https://evaltrack.jesravnbol.dk/latest/repositories/#databricks-volume).
- The documentation site at [evaltrack.jesravnbol.dk](https://evaltrack.jesravnbol.dk), versioned by
  minor release, with an API reference generated from the docstrings.
- `RunRepository`, `RunSummary` and `ReflogEntry` are exported from the package root. They are what
  `open_repository()`, `list_runs()` and `get_ref()` hand back, so a reader can now name every type
  on the repository read path.

## [0.2.0] - 2026-09-15

### Added

- Amazon S3 as a remote repository backend, addressed as `s3://bucket[/prefix]` and installed with
  the `[s3]` extra. Authentication uses boto3's default credential chain. See
  [Repositories › Amazon S3](https://evaltrack.jesravnbol.dk/latest/repositories/#amazon-s3).

## [0.1.0] - 2026-09-11

The first public release. evaltrack is a pytest plugin that records, gates and tracks LLM evals. See
the [README](https://github.com/jesrav/evaltrack#readme) to get started.

[unreleased]: https://github.com/jesrav/evaltrack/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.2.0
[0.1.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.1.0

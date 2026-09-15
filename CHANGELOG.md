# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog], and this project adheres to [Semantic Versioning].

[keep a changelog]: https://keepachangelog.com/en/1.1.0/
[semantic versioning]: https://semver.org/spec/v2.0.0.html

## [0.2.0] - 2026-09-15

### Added

- Amazon S3 as a remote repository backend, addressed as `s3://bucket[/prefix]` and installed with
  the `[s3]` extra. Authentication uses boto3's default credential chain. See
  [Repositories › Amazon S3](docs/repositories.md#amazon-s3).

## [0.1.0] - 2026-09-11

The first public release. evaltrack is a pytest plugin that records, gates and tracks LLM evals. See
the [README](README.md) to get started.

[0.2.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.2.0
[0.1.0]: https://github.com/jesrav/evaltrack/releases/tag/v0.1.0

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-22

### Added

- `firstpr review` — reads a repository's conventions and a diff, writes one
  onboarding comment, optionally posts it to the pull request.
- `firstpr doctor` — prints exactly which convention files, commands and
  credentials were found, and the prompt size, before spending anything.
- `firstpr init` — writes a commented `.firstpr.toml`.
- Idempotent commenting: re-runs edit the existing comment via a hidden marker
  instead of appending duplicates.
- `--only-first-time` gate, which fails closed when contributor history cannot
  be read.
- Diff truncation on file boundaries, and a per-file and overall budget for
  convention excerpts.
- Provider-agnostic: any OpenAI-compatible `/chat/completions` endpoint.

### Security

- The diff and the repository context are decoupled: workflows can keep the
  working tree on the trusted base commit and pass the contributor's commit as
  `--head`, so convention files reaching the model cannot be controlled by the
  pull request.

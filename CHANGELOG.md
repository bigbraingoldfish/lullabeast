# Changelog

This project follows [Semantic Versioning](https://semver.org/). First public release will be tagged `0.1.0`.

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- `SECURITY.md` — security model, vulnerability reporting process, scope, and known limitations
- `CONTRIBUTING.md` — development setup, test commands, PR conventions, skill authoring guidance
- `CHANGELOG.md` — this file

### Changed

- `install.sh` startup echo updated to recommend loopback binding (`127.0.0.1`) rather than `0.0.0.0`

### Fixed

- `AUTODEV_RUNTIME_ROOT` and `AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME` documented in `.env.example`
- `/home/pi/` author paths replaced with neutral placeholders across UI, tests, and docs
- `.claude/settings.json` excluded from git tracking
- `requests` pinned to exact version in dependencies

### Security

- `aiohttp` bumped from 3.13.3 to 3.13.4, resolving 10 CVEs

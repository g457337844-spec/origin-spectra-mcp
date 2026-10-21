# Contributing to Origin Spectra MCP

Thank you for considering a contribution. This repository is being prepared for an independently reviewable open-source release; the private working branch is not itself a published release.

## Scope

Origin/OriginPro MCP for editable XPS/XRD plotting; integrates upstream Ge-Shun/origin-mcp (MIT) and plotting workflows from plotter.

Before opening an issue or pull request, inspect [the project boundaries](docs/ECOSYSTEM.md), [the proposed public release scope](docs/OSS_RELEASE_PLAN.md) and [the security reporting policy](SECURITY.md).

## Development and verification

Install `python -m pip install -e ".[dev]"`; run `python scripts/dev_check.py --tests`. Origin integration tests require an installed, licensed Origin/OriginPro instance.

For patches, include a reproducible failing test or a before/after case where feasible. Report OS, software versions and whether verification used mocks, synthetic data or the actual vendor application. Never claim hardware/vendor integration passed when it was not run.

## PR checklist

- [ ] The change addresses an issue or a clearly reproducible problem.
- [ ] Scope and testing limitations are stated accurately.
- [ ] Private paths, credentials, proprietary binaries and user files are excluded.
- [ ] Attribution and licenses are maintained.
- [ ] Existing tests and relevant documentation are updated.
- [ ] Destructive writes to scientific source data are guarded, backed up and verified.

Do not submit private test materials or proprietary vendor SDK binaries. Maintainers review proposed changes for functional correctness, provenance and data safety.

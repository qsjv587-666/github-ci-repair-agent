# Python Project Benchmarks

These benchmark cases are still synthetic, but they are shaped like small real Python projects instead of single-file fixtures. Each case has its own package layout, CI command, failure log, and eval metadata.

Current scenarios:

- `clinic-profile-contract-pytest`: pytest failure caused by a service/data contract mismatch across modules.
- `ruff-unused-import`: ruff F401 lint failure.
- `mypy-optional-return`: mypy return-value failure caused by returning an optional field as `str`.
- `multifile-profile-contract`: pytest failure where a provider field contract changed and three source consumers still read the old field.
- `multifile-type-propagation`: mypy failure where an optional field propagates through service and notification boundaries.
- `multifile-import-refactor`: pytest import failure where a refactored date parser path must be updated across three call sites.

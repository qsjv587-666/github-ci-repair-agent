# Python Project Benchmarks

These benchmark cases are still synthetic, but they are shaped like small real Python projects instead of single-file fixtures. Each case has its own package layout, CI command, failure log, and eval metadata.

Current scenarios:

- `clinic-profile-contract-pytest`: pytest failure caused by a service/data contract mismatch across modules.
- `ruff-unused-import`: ruff F401 lint failure.
- `mypy-optional-return`: mypy return-value failure caused by returning an optional field as `str`.

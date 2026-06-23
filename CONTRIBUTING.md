# Contributing to MatchRef

Thanks for your interest in improving MatchRef. This document covers how to set
up, the quality bar, and the licensing terms for contributions.

## Licensing of contributions

MatchRef is released under the [Apache License 2.0](LICENSE).

By submitting a contribution (a pull request, patch, or any code/documentation),
you agree that:

1. Your contribution is licensed under the **Apache License 2.0**, the same
   license as the project (inbound = outbound). No separate copyright assignment
   is required — you keep the copyright to your work.
2. You have the right to submit it (it is your original work, or you are
   authorized to contribute it), per the
   [Developer Certificate of Origin 1.1](https://developercertificate.org/).

To certify (2), sign off each commit with `git commit -s`, which appends:

```
Signed-off-by: Your Name <your.email@example.com>
```

## Development setup

```bash
./setup.sh                     # create .venv and install runtime deps
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Quality bar (CI runs all three)

```bash
ruff check .       # lint
mypy               # type check (keep it green; targeted ignores only for cv2/numpy stubs)
pytest -q          # tests
```

Please:

- **Add tests** for new logic. Most of MatchRef is testable without DaVinci
  Resolve — see `tests/` for patterns (stub the Resolve API rather than mocking
  everything). New behavior without a test will usually be asked to add one.
- **Keep functions focused.** The analysis core in `transform_analysis.py` is
  split into small stages on purpose; prefer adding a stage over growing one.
- **Match the surrounding style** — type hints, docstrings explaining the *why*
  for non-obvious transform/timecode math, and English throughout.
- **Don't commit** generated artifacts (`debug/`, `.venv/`, caches) — they are
  gitignored.

## Pull requests

1. Branch off `master`.
2. Make the change with tests; run the three checks above locally.
3. Open a PR; CI (ruff + mypy + pytest) must pass.
4. Keep PRs focused — one logical change per PR is easier to review.

## Project layout

See [`docs/architecture.md`](docs/architecture.md) for the module map and
[`docs/config.md`](docs/config.md) for the full configuration reference.

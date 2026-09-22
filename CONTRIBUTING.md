# Contributing to MixMansion

Thanks for your interest in contributing.

## Before you write code

This project is issue-first: open or pick an existing issue before starting work, and make sure your PR links it (`Closes #N`). This avoids duplicate effort, especially while the connectors (retrievers, categorizers, groupers, namers, writers, stores) are being built out — check the [open issues](https://github.com/janthoXO/MixMansion/issues) first.

For architecture, the domain model, and how to add a new adapter, see [README_DEV.md](README_DEV.md), in particular:

- [How to add an adapter](README_DEV.md#6-how-to-add-an-adapter)
- [Development workflow](README_DEV.md#7-development-workflow)
- [Contributing](README_DEV.md#8-contributing)

## Dev setup, quick version

```bash
uv sync                 # install dependencies
uv run pytest           # run tests
uv run ruff check       # lint
uv run ruff format      # format
uv run lint-imports     # check ports & adapters boundaries
```

The Build workflow runs the same checks on every pull request; all of them must pass.

## Branches and PRs

- Branch names: `feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<issue>-<slug>` (e.g. `feat/12-louvain-grouper`).
- Commits: conventional, imperative mood (`add louvain grouper`, not `added`/`adding`).
- Stacked PRs (e.g. via `gh stack`) are welcome for work that naturally splits into dependent steps.
- Every PR description must include `Closes #N` for the issue it addresses.
- See README_DEV.md's [PR checklist](README_DEV.md#8-contributing) for what to update (tests, README/README_DEV, `.env.example`, `lint-imports`).

## Code of conduct and security

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). If you find a security issue, please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

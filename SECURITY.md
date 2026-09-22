# Security Policy

## Supported Versions

MixMansion is pre-1.0. Only the latest released version is supported with security fixes; please update before reporting an issue that might already be fixed.

## Reporting a Vulnerability

Please do **not** report security issues in a public GitHub issue.

Instead, use GitHub's private vulnerability reporting: go to the [Security tab](https://github.com/janthoXO/MixMansion/security) and click "Report a vulnerability", or use this direct link: https://github.com/janthoXO/MixMansion/security/advisories/new

When reporting, please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce, or a minimal example
- The MixMansion version (`mixmansion --version`) and how it was installed (uv or Docker)
- Any relevant logs (with secrets/tokens removed)

## Response

This is a hobby project maintained on a best-effort basis. There's no guaranteed response time or SLA, but reports will be looked at as soon as reasonably possible.

## Scope

MixMansion caches Spotify OAuth tokens locally in the workspace directory (`.mixmansion/` by default) and reads API keys (Spotify, Last.fm, LLM providers) from a local `.env` file. Reports about these being leaked, logged, or otherwise exposed are in scope.

A few reminders for anyone using or contributing to the project:

- Never commit your `.env` file or anything under `.mixmansion/`.
- Never paste tokens, client secrets, or API keys into an issue, PR, or log output you share — including `--debug` output.

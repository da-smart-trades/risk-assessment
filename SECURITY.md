# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately**. Do not open a public GitHub
issue, pull request, or discussion for a security report.

Preferred channels:

- Use GitHub's **[Private vulnerability reporting](https://github.com/Certora/risk-assessment/security/advisories/new)**
  ("Report a vulnerability" under the repository's Security tab), or
- Email **security@certora.com**.

Please include:

- a description of the issue and its impact,
- steps to reproduce (proof-of-concept if possible),
- affected versions / commit, and
- any suggested remediation.

## What to expect

- We aim to acknowledge reports within a few business days.
- We will keep you informed of progress and coordinate a disclosure timeline.
- Please give us a reasonable opportunity to remediate before any public
  disclosure.

## Scope

Security-relevant areas include authentication and session handling, the
multi-tenant team/authorization model, secret handling, and the deployment
tooling under `infra/`. When self-hosting, review [docs/](docs/) for secure
configuration and treat all secrets in `.env` / your secrets manager as sensitive.

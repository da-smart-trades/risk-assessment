# Contributing

Thanks for your interest in contributing to Certora Blockchain Risk Assessment.

## Getting started

See the [README](README.md) for prerequisites (Python 3.12–3.14 via `uv`, `bun`,
Docker) and the local/Docker quick-start. In short:

```bash
make install        # Python + JS dependencies
make check          # lint + test
```

## Development workflow

1. Fork the repo and create a feature branch off `main`.
2. Make your change. Match the surrounding code style; the standards are enforced
   by the linters, not by hand.
3. Run the quality gates locally before opening a PR:

   ```bash
   make lint       # ruff, ruff-fmt, mypy, pyright, biome, codespell, slotscheck
   make test       # test suite
   make coverage   # aim for 90%+ coverage on new/changed code
   make fmt        # auto-fix formatting (ruff + biome)
   ```

4. Open a pull request against `main` with a clear description of the change and
   why. Keep PRs focused and reasonably small.

## Code standards

- **Python**: type hints everywhere (`T | None`, not `Optional[T]`),
  `from __future__ import annotations`, Google-style docstrings, function-based
  `pytest` tests, timezone-aware datetimes, 88-char lines.
- **TypeScript/React**: functional components with typed props, Tailwind + shadcn/ui,
  Biome for linting/formatting.
- **Licensing**: every new first-party source file must start with the SPDX header:
  - Python: `# SPDX-License-Identifier: AGPL-3.0-only`
  - TS/TSX: `// SPDX-License-Identifier: AGPL-3.0-only`

  followed by `Copyright (C) <year> Certora`. Do not add headers to generated
  code (`resources/lib/generated/`) or third-party/vendored files.

## Licensing of contributions

This project is licensed under the GNU Affero General Public License v3.0
(see [LICENSE](LICENSE)). By submitting a contribution you agree that it is
licensed under the same terms.

## Reporting bugs and security issues

- Regular bugs: open a GitHub issue with steps to reproduce.
- Security vulnerabilities: **do not** open a public issue — follow
  [SECURITY.md](SECURITY.md).

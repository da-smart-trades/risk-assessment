# AI Agent Guidelines for Certora Blockchain Risk Assessment

**Version**: 2.1 (Intelligent Edition) | **Updated**: 2025-12-16

A Certora blockchain risk assessment platform built with Litestar, Inertia.js, React 19, and shadcn/ui providing a seamless SPA experience.

---

## Intelligence Layer

This project uses an **intelligent agent system** that:

1. **Learns from codebase** before making changes
2. **Adapts workflow depth** based on feature complexity
3. **Accumulates knowledge** in pattern library
4. **Selects tools** based on task requirements

### Pattern Library

Reusable patterns in `specs/guides/patterns/`:

- Consult before implementing similar features
- Add new patterns during review phase

### Complexity-Based Checkpoints

| Complexity | Checkpoints | Triggers                               |
| ---------- | ----------- | -------------------------------------- |
| Simple     | 6           | CRUD, config change, single file       |
| Medium     | 8           | New service, API endpoint, 2-3 files   |
| Complex    | 10+         | Architecture change, new domain module |

---

## Quick Reference

### Technology Stack

| Backend                           | Frontend     |
| --------------------------------- | ------------ |
| Python 3.12 – 3.14                | React 19     |
| Litestar 2.19+                    | Inertia.js   |
| advanced-alchemy (SQLAlchemy 2.x) | shadcn/ui    |
| litestar-vite 0.15+               | Tailwind CSS |
| litestar-granian (uvloop)         | Vite         |
| temporalio 1.26+                  | Biome        |
| Alembic (via advanced-alchemy)    | bun / bunx   |
| pytest                            |              |

Runtime CLI entry: `certora-risk-api` (wraps Litestar CLI — [src/cert_ra/api/cli.py](src/cert_ra/api/cli.py)). Additional scripts: `certora-risk-dashboard`, `certora-risk-ingester`.

### Essential Commands

All linting/testing/build goes through `make`. Python tools run under `uv run`, JS tools under `bun`/`bunx`. The Makefile is organized into sections — run `grep '^# ===' Makefile -A1` to see them.

```bash
# Install
make install        # Install Python + JS dependencies (uv sync + bun install)
make install-js     # JS deps only

# Linting — aggregate
make check          # Default: lint + test
make lint           # pre-commit (ruff, ruff-fmt, mypy, codespell, biome) + pyright + slotscheck
make lint-py        # Python-only linting (ruff, type-check, slotscheck, codespell)
make lint-js        # JS-only linting (biome)
make pre-commit     # Run prek hooks
make fmt            # Auto-fix: ruff-fix + ruff-fmt + biome-fix

# Linting — Python (individual)
make ruff           # Ruff lint
make ruff-fmt-check # Ruff format check (no writes)
make type-check     # mypy + pyright
make mypy
make pyright
make slotscheck
make codespell

# Linting — JS (individual)
make biome          # Biome check (read-only)
make biome-fix      # Biome check --write (applies fixes)

# Testing
make test           # Fast suite (parallel, no coverage)
make test-all       # Full suite
make coverage       # With HTML coverage report
make pytest         # Plain pytest run

# Build
make build          # Python wheel (uv build)
make build-js       # Vite production build

# Dev servers
make dev-js         # Vite dev server
make dashboard      # Dashboard uvicorn server
make ingester       # Start ingester worker

# Type Generation / Database / App
uv run certora-risk-api assets generate-types       # Generate TS types from OpenAPI
uv run certora-risk-api assets build                # REQUIRED after any structural UI changes (new pages, routes, nav)
uv run certora-risk-api database upgrade            # Apply migrations
uv run certora-risk-api database make-migrations    # Create migration
uv run certora-risk-api run                         # Start app with Granian
```

See [Makefile](Makefile) for the full list including requirements export, security audit, and cleanup targets.

---

## Code Standards

### Python

| Rule               | Standard                                            |
| ------------------ | --------------------------------------------------- |
| Type hints         | Use `T \| None` (PEP 604), not `Optional[T]`        |
| Future annotations | `from __future__ import annotations` in all files   |
| Docstrings         | Google style                                        |
| Tests              | Function-based pytest (not class-based)             |
| Line length        | 88 characters                                       |
| Datetime           | Always timezone-aware: `datetime.now(timezone.utc)` |

### TypeScript/React

| Rule       | Standard                              |
| ---------- | ------------------------------------- |
| Linting    | Biome                                 |
| Components | Functional with TypeScript interfaces |
| Styling    | Tailwind CSS with shadcn/ui           |
| State      | Inertia.js `useForm`, `usePage`       |

---

## Slash Commands

| Command             | Description                         |
| ------------------- | ----------------------------------- |
| `/prd [feature]`    | Create PRD with pattern learning    |
| `/implement [slug]` | Pattern-guided implementation       |
| `/test [slug]`      | Testing with 90%+ coverage          |
| `/review [slug]`    | Quality gate and pattern extraction |
| `/explore [topic]`  | Explore codebase                    |
| `/fix-issue [#]`    | Fix GitHub issue                    |

---

## Subagents

| Agent         | Mission                                |
| ------------- | -------------------------------------- |
| `prd`         | PRD creation with pattern recognition  |
| `expert`      | Implementation with pattern compliance |
| `testing`     | Test creation (90%+ coverage)          |
| `docs-vision` | Quality gates and pattern extraction   |

---

## Architecture

### Backend Structure

```
src/cert_ra/
├── api/
│   ├── asgi.py           # create_app factory (LITESTAR_APP entrypoint)
│   ├── cli.py            # certora-risk-api script (wraps Litestar CLI)
│   ├── core.py           # ApplicationCore plugin, route registration
│   ├── config.py         # Configuration
│   ├── domain/
│   │   ├── accounts/     # User auth & profiles (controllers/, services/, guards.py, dependencies.py, schemas.py)
│   │   ├── admin/        # Admin panel
│   │   ├── teams/        # Multi-tenant teams
│   │   ├── tags/         # Tagging system
│   │   ├── metrics/      # Metrics API (finality snapshots, read-only pagination)
│   │   ├── web/          # Inertia page controllers
│   │   ├── listeners.py  # Cross-domain event listeners
│   │   └── routes.py     # Aggregated route registration
│   └── lib/              # Shared: crypt.py, dto.py, email.py, exceptions.py, log.py, oauth.py, schema.py, vite.py (Inertia config)
├── db/
│   ├── engine_factory.py
│   ├── storage.py
│   ├── utils.py
│   ├── fixtures/         # Seed data
│   ├── migrations/       # Alembic migrations
│   └── models/           # SQLAlchemy models (user, team, tag, role, finality/, ...)
├── metrics/              # Temporal workflows + worker
│   ├── worker.py         # TASK_QUEUE = "metrics" — Temporal worker entrypoint
│   └── finality/         # Finality workflow + activities
├── settings/             # pydantic-settings modules
│   ├── api.py            # API / app settings
│   ├── db.py             # Database settings
│   ├── rpc.py            # RPC / chain client settings
│   └── temporal.py       # TemporalSettings (env_prefix=cert_ra_temporal_)
├── resources/            # Packaged Python resources (static assets, templates)
├── log.py                # Structlog setup
├── types.py              # Shared type aliases
└── utils.py              # Shared helpers
```

### Frontend Structure

```
resources/
├── pages/            # Inertia pages
│   ├── dashboard.tsx
│   ├── landing.tsx
│   ├── about.tsx
│   ├── error.tsx
│   ├── admin/        # Admin screens
│   ├── auth/         # Login, register, 2FA, password reset
│   ├── chain/        # Chain / metrics views
│   ├── invitation/
│   ├── legal/
│   ├── profile/
│   └── team/
├── components/
│   ├── ui/           # shadcn/ui primitives
│   ├── app-sidebar.tsx, settings-sidebar.tsx
│   ├── data-table.tsx, container.tsx, header.tsx
│   ├── nav-main.tsx, nav-user.tsx, team-switcher.tsx
│   ├── theme-provider.tsx, theme-toggle.tsx
│   └── auth-hero-panel.tsx, input-error.tsx, logo.tsx, icons.tsx
├── layouts/
│   ├── app-layout.tsx
│   ├── admin-layout.tsx
│   ├── guest-layout.tsx
│   └── partials/     # Header/sidebar fragments
├── hooks/            # Shared React hooks
├── lib/              # Frontend utilities (generated API SDK lives here)
├── assets/           # Static assets bundled by Vite
├── main.tsx          # App entry point
├── main.css          # Tailwind v4 CSS-first config
├── index.html        # Vite HTML template
└── vite-env.d.ts
```

---

## Key Patterns

### Inertia Page Controller (Preferred Style)

Use `component` kwarg in route decorator:

```python
from litestar import Controller, get, post
from litestar_vite.inertia import InertiaRedirect

class FeatureController(Controller):
    path = "/feature"

    @get(component="feature/list", path="/", name="feature.list")
    async def list(self, service: FeatureService) -> dict:
        items = await service.list()
        return {"items": items}

    @post(component="feature/create", path="/", name="feature.create")
    async def create(self, request: Request, data: CreateSchema, service: FeatureService) -> InertiaRedirect:
        await service.create(data.to_dict())
        return InertiaRedirect(request, request.url_for("feature.list"))
```

### Service Pattern

```python
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from cert_ra.db.models import FeatureModel
from cert_ra.api.domain.feature.repositories import FeatureRepository

class FeatureService(SQLAlchemyAsyncRepositoryService[FeatureModel]):
    repository_type = FeatureRepository
```

### Inertia React Page

```tsx
import { Head } from "@inertiajs/react";
import AppLayout from "@/layouts/app-layout";

interface Props {
  items: Item[];
}

export default function List({ items }: Props) {
  return (
    <AppLayout>
      <Head title="Feature List" />
      {/* Content using shadcn/ui */}
    </AppLayout>
  );
}
```

---

## Quality Gates

All code must pass:

- [ ] `make test` passes
- [ ] `make lint` passes (covers Python + JS: prek hooks, pyright, slotscheck)
- [ ] `make lint-js` (or the broader `make lint`) — Biome clean on `resources/`
- [ ] 90%+ coverage for modified modules

---

## MCP Tools

### DeepWiki (Library Docs)

```python
mcp__deepwiki__ask_question(
    repoName="litestar-org/litestar",
    question="How do I register dependencies at the controller scope?"
)

mcp__deepwiki__read_wiki_structure(repoName="inertiajs/inertia")
mcp__deepwiki__read_wiki_contents(repoName="facebook/react")
```

### Commonly Used Repos

- Litestar: `litestar-org/litestar`
- Litestar Vite/Inertia plugin: `litestar-org/litestar-vite`
- advanced-alchemy: `litestar-org/advanced-alchemy`
- SQLAlchemy: `sqlalchemy/sqlalchemy`
- React: `facebook/react`
- Inertia.js: `inertiajs/inertia`
- shadcn/ui: `shadcn-ui/ui`
- Temporal Python SDK: `temporalio/sdk-python`

### Tool Selection

Consult `.claude/mcp-strategy.md` for task-based tool selection.

---

## Anti-Patterns (Must Avoid)

| Pattern                | Use Instead                    |
| ---------------------- | ------------------------------ |
| `Optional[T]`          | `T \| None`                    |
| `datetime.now()`       | `datetime.now(UTC)`            |
| `class TestFoo:`       | Function-based tests           |
| Direct InertiaResponse | `component` kwarg in decorator |
| Missing type hints     | Always use type hints          |

---

## Development Workflow

### For New Features

1. **PRD**: `/prd [feature]` - Pattern analysis first
2. **Implement**: `/implement [slug]` - Follow patterns
3. **Test**: Auto-invoked - 90%+ coverage
4. **Review**: Auto-invoked - Pattern extraction

### Quick Tasks

1. Search pattern library first
2. Read 3-5 similar implementations
3. Follow existing patterns
4. Run quality gates before committing

---

## Key Configuration Files

| File                          | Purpose                                    |
| ----------------------------- | ------------------------------------------ |
| `src/cert_ra/api/core.py`     | ApplicationCore plugin, route registration |
| `src/cert_ra/api/lib/vite.py` | Inertia configuration                      |
| `pyproject.toml`              | Python dependencies, tool configs          |
| `package.json`                | Frontend dependencies                      |
| `vite.config.ts`              | Vite + litestar-vite-plugin config         |
| `components.json`             | shadcn/ui configuration                    |

---

## Additional Instructions

1. Multiple agents may be working simultaneously. If you see build errors in files you did NOT edit, do not try to fix them. Wait 30 seconds and retry the build - the other agent is likely mid-edit.
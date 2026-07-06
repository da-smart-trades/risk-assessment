# Architecture

A risk-monitoring platform for blockchain networks and tokens, built with Litestar,
Inertia.js + React 19, PostgreSQL, and Temporal.

## Technology stack

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

Runtime CLI entry point: `certora-risk-api` (wraps the Litestar CLI —
[src/cert_ra/api/cli.py](../src/cert_ra/api/cli.py)). Additional scripts:
`certora-risk-dashboard`, `certora-risk-ingester`.

All linting/testing/build goes through `make`; Python tools run under `uv run`, JS
tools under `bun`/`bunx`. See the [Makefile](../Makefile) and [README](../README.md)
for the command list.

## Backend structure

```
src/cert_ra/
├── api/
│   ├── asgi.py           # create_app factory (LITESTAR_APP entrypoint)
│   ├── cli.py            # certora-risk-api script (wraps Litestar CLI)
│   ├── core.py           # ApplicationCore plugin, route registration
│   ├── config.py         # Configuration
│   ├── domain/
│   │   ├── accounts/     # User auth & profiles
│   │   ├── admin/        # Admin panel
│   │   ├── teams/        # Multi-tenant teams
│   │   ├── tags/         # Tagging system
│   │   ├── metrics/      # Metrics API (finality snapshots, read-only pagination)
│   │   ├── web/          # Inertia page controllers
│   │   ├── listeners.py  # Cross-domain event listeners
│   │   └── routes.py     # Aggregated route registration
│   └── lib/              # Shared: crypt, dto, email, exceptions, log, oauth, schema, vite
├── db/
│   ├── engine_factory.py
│   ├── storage.py
│   ├── fixtures/         # Seed data
│   ├── migrations/       # Alembic migrations
│   └── models/           # SQLAlchemy models
├── metrics/              # Temporal workflows + worker
│   ├── worker.py         # TASK_QUEUE = "metrics" — Temporal worker entrypoint
│   └── finality/         # Finality workflow + activities
├── settings/             # pydantic-settings modules (api, db, rpc, temporal)
├── log.py                # Structlog setup
├── types.py              # Shared type aliases
└── utils.py              # Shared helpers
```

## Frontend structure

```
resources/
├── pages/            # Inertia pages (dashboard, landing, about, admin/, auth/,
│                     #   chain/, invitation/, legal/, profile/, team/)
├── components/       # ui/ (shadcn primitives) + app components
├── layouts/          # app / admin / guest layouts + partials
├── hooks/            # Shared React hooks
├── lib/              # Frontend utilities (generated API SDK lives in lib/generated)
├── assets/           # Static assets bundled by Vite
├── main.tsx          # App entry point
├── main.css          # Tailwind v4 CSS-first config
└── index.html        # Vite HTML template
```

## Key patterns

### Inertia page controller

Use the `component` kwarg in the route decorator:

```python
from litestar import Controller, get, post
from litestar_vite.inertia import InertiaRedirect

class FeatureController(Controller):
    path = "/feature"

    @get(component="feature/list", path="/", name="feature.list")
    async def list(self, service: FeatureService) -> dict:
        return {"items": await service.list()}

    @post(component="feature/create", path="/", name="feature.create")
    async def create(self, request: Request, data: CreateSchema, service: FeatureService) -> InertiaRedirect:
        await service.create(data.to_dict())
        return InertiaRedirect(request, request.url_for("feature.list"))
```

### Service pattern

```python
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from cert_ra.db.models import FeatureModel
from cert_ra.api.domain.feature.repositories import FeatureRepository

class FeatureService(SQLAlchemyAsyncRepositoryService[FeatureModel]):
    repository_type = FeatureRepository
```

### Inertia React page

```tsx
import { Head } from "@inertiajs/react";
import AppLayout from "@/layouts/app-layout";

export default function List({ items }: { items: Item[] }) {
  return (
    <AppLayout>
      <Head title="Feature List" />
      {/* Content using shadcn/ui */}
    </AppLayout>
  );
}
```

## Code standards

### Python

| Rule               | Standard                                            |
| ------------------ | --------------------------------------------------- |
| Type hints         | `T \| None` (PEP 604), not `Optional[T]`            |
| Future annotations | `from __future__ import annotations` in all files   |
| Docstrings         | Google style                                        |
| Tests              | Function-based pytest (not class-based)             |
| Line length        | 88 characters                                       |
| Datetime           | Timezone-aware: `datetime.now(timezone.utc)`        |

### TypeScript / React

| Rule       | Standard                              |
| ---------- | ------------------------------------- |
| Linting    | Biome                                 |
| Components | Functional with TypeScript interfaces |
| Styling    | Tailwind CSS with shadcn/ui           |
| State      | Inertia.js `useForm`, `usePage`       |

### Anti-patterns to avoid

| Pattern                | Use instead                    |
| ---------------------- | ------------------------------ |
| `Optional[T]`          | `T \| None`                    |
| `datetime.now()`       | `datetime.now(UTC)`            |
| `class TestFoo:`       | Function-based tests           |
| Direct InertiaResponse | `component` kwarg in decorator |

## Key configuration files

| File                          | Purpose                                    |
| ----------------------------- | ------------------------------------------ |
| `src/cert_ra/api/core.py`     | ApplicationCore plugin, route registration |
| `src/cert_ra/api/lib/vite.py` | Inertia configuration                      |
| `pyproject.toml`              | Python dependencies, tool configs          |
| `package.json`                | Frontend dependencies                      |
| `vite.config.ts`              | Vite + litestar-vite-plugin config         |
| `components.json`             | shadcn/ui configuration                    |
| `deployment.config.json`      | Deployer AWS/GitHub/DNS values (see `.example`) |

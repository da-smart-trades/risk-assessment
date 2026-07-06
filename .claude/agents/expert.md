---
name: expert
description: Implementation specialist with pattern compliance. Use for implementing features from PRDs following established patterns.
tools: Read, Write, Edit, Glob, Grep, Bash, Task, WebSearch, mcp__deepwiki__read_wiki_structure, mcp__deepwiki__read_wiki_contents, mcp__deepwiki__ask_question, mcp__pal__thinkdeep, mcp__pal__debug
model: opus
---

# Expert Implementation Agent

**Mission**: Write production-quality code following identified patterns and project conventions.

## Intelligence Layer

Before implementation:

1. **Load PRD**: `specs/active/{slug}/prd.md`
2. **Load patterns**: From PRD research and pattern library
3. **Load tasks**: `specs/active/{slug}/tasks.md`
4. **Identify similar code**: Read 3-5 reference files

## Litestar-Specific Patterns

### Controller Pattern

```python
from litestar import Controller, get, post, delete, patch
from litestar.di import Provide
from litestar_vite.inertia import InertiaRedirect

class FeatureController(Controller):
    path = "/feature"
    dependencies = {"service": Provide(provide_service)}

    @get(component="feature/list", path="/", name="feature.list")
    async def list(self, service: FeatureService) -> dict:
        items = await service.list()
        return {"items": [item.to_dict() for item in items]}

    @post(component="feature/create", path="/", name="feature.create")
    async def create(
        self, request: Request, data: CreateDTO, service: FeatureService
    ) -> InertiaRedirect:
        item = await service.create(data)
        return InertiaRedirect(request, request.url_for("feature.show", item_id=item.id))
```

### Service Pattern

```python
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from cert_ra.db.models import FeatureModel
from cert_ra.api.domain.feature.repositories import FeatureRepository

class FeatureService(SQLAlchemyAsyncRepositoryService[FeatureModel]):
    repository_type = FeatureRepository

    async def custom_business_logic(self, data: dict) -> FeatureModel:
        # Custom logic here
        return await self.create(data)
```

### Repository Pattern

```python
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from cert_ra.db.models import FeatureModel

class FeatureRepository(SQLAlchemyAsyncRepository[FeatureModel]):
    model_type = FeatureModel
```

### Inertia Page Pattern

```tsx
import { Head } from "@inertiajs/react";
import AppLayout from "@/layouts/app-layout";

interface Props {
  items: Item[];
}

export default function List({ items }: Props) {
  return (
    <AppLayout>
      <Head title="Feature" />
      <div className="container mx-auto py-6">{/* Content using shadcn/ui components */}</div>
    </AppLayout>
  );
}
```

## Implementation Workflow

### 1. Load Context

```bash
cat specs/active/{slug}/prd.md
cat specs/active/{slug}/tasks.md
```

### 2. Pattern Deep Dive

Read 3-5 similar implementations:

- Controller structure
- Service methods
- Repository usage
- Inertia page props

### 3. Database Layer (if needed)

Create models following existing patterns:

- Use `__tablename__` with snake_case
- Include proper relationships
- Export in `__init__.py`

Create migration:

```bash
uv run certora-risk-api database make-migrations
uv run certora-risk-api database upgrade
```

### 4. Repository & Service

Follow advanced-alchemy patterns:

- Extend base repository
- Extend SQLAlchemyAsyncRepositoryService
- Add custom methods as needed

### 5. Controller

Follow Litestar patterns:

- Use route decorators
- Inject services via dependencies
- Return InertiaResponse for pages

### 6. Frontend

Create Inertia pages:

- Receive typed props
- Use shadcn/ui components
- Follow layout patterns

### 7. Quality Gates

After each major step:

```bash
make test
make type-check
```

### 8. Document Patterns

Add new patterns to `specs/active/{slug}/tmp/new-patterns.md`.

## Auto-Invoke Testing

After implementation, invoke testing agent:

```
Task(
    subagent_type="testing",
    prompt="Create comprehensive tests for {slug} feature..."
)
```

## Output Format

```
Implementation Complete ✓

Feature: {slug}
Files Created/Modified: [list]
Tests: [pass/fail]
Coverage: [N]%

Pattern Compliance:
- ✓ Controller follows domain pattern
- ✓ Service extends SQLAlchemyAsyncRepositoryService
- ✓ Inertia pages receive typed props

Quality Gates:
- ✓ make test passes
- ✓ make lint passes

New Patterns Documented: [N]

Ready for: /review {slug}
```

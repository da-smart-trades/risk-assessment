# Litestar Framework Skill

## Quick Reference

### Controller

Use `component` kwarg in route decorator — **not** `InertiaResponse` directly:

```python
from litestar import Controller, get, post, delete, patch, put
from litestar.di import Provide
from litestar_vite.inertia import InertiaRedirect

class FeatureController(Controller):
    path = "/feature"
    tags = ["Feature"]

    @get(component="feature/list", path="/", name="feature.list")
    async def list(self, service: FeatureService) -> dict:
        items = await service.list()
        return {"items": items}

    @get(component="feature/show", path="/{item_id:uuid}", name="feature.show")
    async def show(
        self,
        item_id: UUID,
        service: FeatureService,
    ) -> FeatureSchema:
        item = await service.get(item_id)
        return service.to_schema(item, schema_type=FeatureSchema)

    @post(component="feature/create", path="/", name="feature.create")
    async def create(
        self,
        request: Request,
        data: CreateDTO,
        service: FeatureService,
    ) -> InertiaRedirect:
        item = await service.create(data)
        return InertiaRedirect(request, request.url_for("feature.show", item_id=item.id))
```

### Dependency Injection

```python
from litestar.di import Provide

def provide_service(db_session: AsyncSession) -> FeatureService:
    return FeatureService(session=db_session)

# In controller
dependencies = {"service": Provide(provide_service)}
```

### Guards

```python
from litestar.connection import ASGIConnection
from litestar.handlers import BaseRouteHandler

async def require_auth(
    connection: ASGIConnection,
    _: BaseRouteHandler,
) -> None:
    if not connection.user:
        raise PermissionDeniedException("Authentication required")
```

### Inertia Redirect

```python
from litestar_vite.inertia import InertiaRedirect, InertiaExternalRedirect

# Internal redirect (after create/update/delete)
return InertiaRedirect(request, request.url_for("feature.list"))

# External redirect (OAuth, external URLs)
return InertiaExternalRedirect(request, redirect_to="https://example.com/oauth/...")
```

## Project Patterns

### Controller Location

`src/cert_ra/api/domain/{feature}/controllers.py`

### Service Location

`src/cert_ra/api/domain/{feature}/services.py`

### Registering Controllers

In `src/cert_ra/api/core.py`:

```python
app_config.route_handlers.extend([
    FeatureController,
])
```

## DeepWiki Lookup

```python
mcp__deepwiki__ask_question(
    repoName="litestar-org/litestar",       # or: litestar-org/advanced-alchemy
    question="How do I write a Guard that accesses the request state?"
)
```

## Related Files

- `src/cert_ra/api/core.py` - Controller registration
- `src/cert_ra/api/domain/accounts/controllers.py` - Controller example
- `src/cert_ra/api/domain/accounts/guards.py` - Guard example
- `src/cert_ra/api/lib/dependencies.py` - Dependency providers

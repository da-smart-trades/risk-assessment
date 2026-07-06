# Temporal.io Skill

## Quick Reference

### Workflow

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class FinalityCheckWorkflow:
    @workflow.run
    async def run(self, params: FinalityCheckParams) -> FinalityCheckResult:
        return await workflow.execute_activity(
            fetch_finality_snapshot,
            params,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(minutes=1),
                maximum_attempts=5,
            ),
        )
```

**Rules:**

- `@workflow.defn` on class, `@workflow.run` on exactly one `async def run(self, ...)`
- Single dataclass parameter — never multiple positional args
- No `datetime.now()`, no HTTP/DB, no `asyncio.sleep()` — use `workflow.*` equivalents

### Activity

```python
from temporalio import activity


@activity.defn
async def fetch_finality_snapshot(params: FinalityCheckParams) -> FinalitySnapshot:
    info = activity.info()  # workflow_id, attempt, activity_type
    data = await chain_client.query(params.chain)
    return FinalitySnapshot.from_raw(data)
```

### Parallel Activities

```python
import asyncio

results = await asyncio.gather(
    workflow.execute_activity(fetch_eth, params, start_to_close_timeout=timedelta(minutes=2)),
    workflow.execute_activity(fetch_sol, params, start_to_close_timeout=timedelta(minutes=2)),
)
```

### Heartbeat (long-running activities)

```python
@activity.defn
async def index_blocks(params: IndexParams) -> IndexResult:
    info = activity.info()
    start = info.heartbeat_details[0] if info.heartbeat_details else params.start

    for i in range(start, params.end):
        if activity.is_cancelled():
            raise asyncio.CancelledError()
        await process_block(i)
        if i % 100 == 0:
            activity.heartbeat(i)
```

### Signals & Queries

```python
@workflow.defn
class PollingWorkflow:
    def __init__(self) -> None:
        self._stop = False

    @workflow.signal
    async def stop(self) -> None:
        self._stop = True

    @workflow.query
    def is_stopped(self) -> bool:
        return self._stop

    @workflow.run
    async def run(self, params: Params) -> Result:
        while not self._stop:
            await workflow.execute_activity(poll_chain, params, start_to_close_timeout=timedelta(minutes=1))
            await workflow.sleep(timedelta(minutes=5))
        return Result(status="stopped")
```

### Versioning (safe code changes)

```python
if workflow.patched("new-snapshot-format"):
    result = await workflow.execute_activity(fetch_v2, ...)
else:
    result = await workflow.execute_activity(fetch_v1, ...)
```

## Project Patterns

### File Layout

```
src/cert_ra/metrics/{domain}/
├── workflows.py    # @workflow.defn only
├── activities.py   # @activity.defn only
└── schemas.py      # dataclasses for inputs/outputs
```

### Task Queue

All metrics workers use `"metrics"`. Defined in `src/cert_ra/metrics/worker.py`.

### Workflow ID Convention

```python
id = f"finality-{chain}-{date.today().isoformat()}"   # scheduled
id = f"finality-{chain}-block-{block_height}"          # event-triggered
```

### Retry Policy Defaults

```python
CHAIN_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=10,
    non_retryable_error_types=["ValidationError", "AuthenticationError"],
)
```

### Triggering from Litestar

```python
# In a controller — never await handle.result() in an HTTP handler
handle = await state.temporal.start_workflow(
    FinalityCheckWorkflow.run,
    params,
    id=f"finality-{params.chain}-{date.today().isoformat()}",
    task_queue="metrics",
    execution_timeout=timedelta(hours=1),
)
return {"workflow_id": handle.id}
```

### Registering Worker

In `src/cert_ra/metrics/worker.py`:

```python
worker = Worker(
    client,
    task_queue="metrics",
    workflows=[FinalityCheckWorkflow],
    activities=[fetch_finality_snapshot, store_finality_snapshot],
)
```

### Settings

`TemporalSettings` in [src/cert_ra/settings/temporal.py](../../../src/cert_ra/settings/temporal.py) — env prefix `CERT_RA_TEMPORAL_` (case-insensitive):

| Env var                      | Dev default      | Production                   |
| ---------------------------- | ---------------- | ---------------------------- |
| `CERT_RA_TEMPORAL_HOST`      | `localhost:7233` | `<account>.tmprl.cloud:7233` |
| `CERT_RA_TEMPORAL_NAMESPACE` | `default`        | `<ns>.<accountid>`           |
| `CERT_RA_TEMPORAL_API_KEY`   | _(empty)_        | Temporal Cloud API key       |

TLS enabled automatically when `api_key` is set.

## Determinism Cheatsheet

| ❌ Forbidden      | ✅ Use instead                                   |
| ----------------- | ------------------------------------------------ |
| `datetime.now()`  | `workflow.now()`                                 |
| `asyncio.sleep()` | `await workflow.sleep(timedelta(...))`           |
| `random.random()` | `random.Random(workflow.random_seed()).random()` |
| HTTP / DB calls   | Activity functions                               |

## DeepWiki Lookup

```python
mcp__deepwiki__ask_question(
    repoName="temporalio/sdk-python",
    question="How do I use `workflow.continue_as_new` with updated params?"
)
```

For authoritative Temporal Cloud / platform docs, the project also has a `temporal-docs` MCP server (kapa.ai-backed RAG) in `.mcp.json`. Prefer DeepWiki for SDK usage questions; use `temporal-docs` for platform/cluster/operator topics.

## Related Files

- `specs/guides/temporal.md` — Full integration guide
- `specs/guides/patterns/temporal-patterns.md` — Project-specific patterns
- `src/cert_ra/metrics/worker.py` — Worker entrypoint
- `src/cert_ra/settings/api.py` — TemporalSettings

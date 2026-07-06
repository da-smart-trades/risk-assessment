---
description: Explore codebase for patterns and understanding
allowed-tools: Read, Glob, Grep, Bash, Task, mcp__deepwiki__read_wiki_structure, mcp__deepwiki__read_wiki_contents, mcp__deepwiki__ask_question
---

# Codebase Exploration

You are exploring: **$ARGUMENTS**

## Exploration Modes

Based on the query, use the appropriate exploration mode:

### Mode 1: Architecture Overview

**Trigger**: "architecture", "structure", "overview", "how does"

```bash
# Project structure
tree -L 2 -d src/cert_ra/ resources/

# Core configuration
cat src/cert_ra/api/server/core.py
cat src/cert_ra/settings/
```

### Mode 2: Feature Discovery

**Trigger**: "where is", "find", "locate"

```bash
# Search for patterns
grep -r "{pattern}" src/cert_ra/ --include="*.py"
grep -r "{pattern}" resources/ --include="*.tsx"

# Find files
find src/cert_ra/ -name "*{keyword}*"
find resources/ -name "*{keyword}*"
```

### Mode 3: Pattern Understanding

**Trigger**: "how to", "pattern", "example"

```bash
# Read existing implementations
cat src/cert_ra/api/domain/accounts/controllers.py
cat src/cert_ra/api/domain/accounts/services.py
cat resources/pages/dashboard.tsx
```

### Mode 4: Dependency Analysis

**Trigger**: "uses", "depends on", "related to"

```bash
# Find imports
grep -r "from src.cert_ra.api.domain.{feature}" src/cert_ra/api/domain/
grep -r "import.*{feature}" resources/
```

---

## Quick Reference Exploration

### Backend Architecture

**Controllers** (Litestar with Inertia):

```bash
ls src/cert_ra/api/domain/*/controllers.py
cat src/cert_ra/api/domain/accounts/controllers.py | head -100
```

**Services** (advanced-alchemy):

```bash
ls src/cert_ra/api/domain/*/services.py
cat src/cert_ra/api/domain/accounts/services.py | head -100
```

**Models** (SQLAlchemy 2.0):

```bash
ls src/cert_ra/db/models/
cat src/cert_ra/db/models/user.py
```

**Repositories**:

```bash
ls src/cert_ra/api/domain/*/repositories.py
cat src/cert_ra/api/domain/accounts/repositories.py | head -50
```

### Frontend Architecture

**Pages** (React/Inertia):

```bash
ls resources/pages/
cat resources/pages/dashboard.tsx
```

**Components** (shadcn/ui):

```bash
ls resources/components/ui/
cat resources/components/ui/button.tsx
```

**Layouts**:

```bash
ls resources/layouts/
cat resources/layouts/app-layout.tsx
```

### Configuration

**App config**:

```bash
cat src/cert_ra/settings/app.py | head -100
```

**Plugins**:

```bash
cat src/cert_ra/api/server/plugins.py
```

**Settings**:

```bash
cat src/cert_ra/settings/*.py
```

---

## Library Documentation Lookup

Use DeepWiki MCP for library-specific questions:

```
mcp__deepwiki__ask_question(
    repoName="litestar-org/litestar",
    question="{specific question}"
)
```

**Available repos**:

- Litestar: `litestar-org/litestar`
- Litestar Vite/Inertia: `litestar-org/litestar-vite`
- SQLAlchemy: `sqlalchemy/sqlalchemy`
- advanced-alchemy: `litestar-org/advanced-alchemy`
- React: `facebook/react`
- Inertia.js: `inertiajs/inertia`
- shadcn/ui: `shadcn-ui/ui`
- Temporal Python SDK: `temporalio/sdk-python`

Fallback: WebSearch.

---

## Deep Exploration with Subagent

For complex explorations, use the Explore subagent:

```
Task(
    subagent_type="Explore",
    prompt="Explore {topic} in this codebase. Find all related files, understand the patterns, and explain how it works."
)
```

---

## Output Format

After exploration, provide:

```markdown
## Exploration: {topic}

### Summary

[Brief overview of what was found]

### Key Files

- `path/to/file.py` - Description
- `path/to/file.tsx` - Description

### Patterns Found

1. Pattern description
2. Pattern description

### Code Examples

[Relevant code snippets]

### Related Documentation

[Links or references to docs]
```

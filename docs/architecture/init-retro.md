# Retro: Filling Architecture Docs After `/init`

## What was the situation

`/init` (via `dev-core:init`) ran on voiceCLI and created the full scaffolding:

```
docs/
├── architecture/
│   └── README.md              ← 3-line stub ("see CLAUDE.md")
├── standards/
│   ├── backend-patterns.md    ← 3-line stub
│   ├── testing.md             ← 13-line stub
│   └── code-review.md        ← 4-line stub
├── contributing.md            ← 1-line stub
└── configuration.md           ← 2-line stub
```

`.claude/stack.yml` was correctly configured with all `standards.*` paths pointing to these files. The paths existed, the files existed, but they contained almost nothing — just "Stub — see CLAUDE.md" or "Stub — see CONTRIBUTING.md".

**Impact:** All dev-core agents (architect, backend-dev, reviewer, tester) read these files via `{standards.architecture}`, `{standards.backend}`, etc. They found stubs instead of real guidance. The architect agent wouldn't fail fast (paths existed), but it had zero architectural context to inform decisions.

Meanwhile, **CLAUDE.md had all the real content** — detailed pipeline docs, engine capability matrix, CLI commands, conventions. The stubs just pointed back to it, creating a circular reference that agents couldn't use effectively.

## What was done

### Phase 1: Audit (what exists vs what's needed)

1. **Searched voiceCLI** for any existing architecture/patterns documentation → found only stubs
2. **Analyzed the actual codebase** to identify patterns already in use (but undocumented):
   - Read `engine.py` (ABC + registry), `api.py` (orchestration), `translate.py` (capability matrix), `markdown.py` (domain models), `config.py` (TOML loader)
   - Mapped modules to architectural layers
   - Identified implicit patterns: Strategy+Registry, Adapter/Translator, Data Pipeline, Lazy Loading, Library-First API

### Phase 2: Reference (what does the boilerplate do?)

3. **Read roxabi-boilerplate docs** to understand the target format and structure:
   - `docs/architecture/index.mdx` (213 lines) — structure + Mermaid diagrams
   - `docs/architecture/backend-ddd-hexagonal.mdx` (374 lines) — patterns with code examples + migration guide
   - `docs/architecture/ubiquitous-language.mdx` (72 lines) — glossary + lifecycle diagrams
   - `docs/standards/backend-patterns.mdx` (531 lines) — conventions + SOLID + AI Quick Reference
   - `docs/standards/testing.mdx` (789 lines) — test philosophy + mocking strategies
   - `docs/standards/code-review.mdx` (168 lines) — checklists + Conventional Comments

4. **Read dev-core agent definitions** to understand the linkage:
   - Architect agent reads `{standards.architecture}` — fails fast if undefined
   - All agents share the same `stack.yml` standards paths
   - Agents use "AI Quick Reference" sections for compressed rules

### Phase 3: Create (adapt boilerplate format to voiceCLI content)

5. **Created 6 substantive docs** (1,373 lines total, replacing ~30 lines of stubs):

| File | Lines | Adapted from boilerplate |
|------|-------|--------------------------|
| `docs/architecture/index.mdx` | 167 | `architecture/index.mdx` format — project structure tree, layer diagram (Mermaid), data flow diagram, dependency direction table |
| `docs/architecture/patterns.mdx` | 392 | `architecture/backend-ddd-hexagonal.mdx` format — but Python/CLI patterns instead of DDD/NestJS. Strategy+Registry, Adapter/Translator, Pipeline, Lazy Loading, SOLID. Includes "When to adopt more structure" signals table |
| `docs/architecture/ubiquitous-language.mdx` | 91 | `architecture/ubiquitous-language.mdx` format — glossary table, common confusions, Mermaid lifecycle diagrams |
| `docs/standards/backend-patterns.md` | 286 | `standards/backend-patterns.mdx` format — code organization, design patterns table, error handling layers, SOLID principles, AI Quick Reference section |
| `docs/standards/testing.md` | 211 | `standards/testing.mdx` format — philosophy, AAA pattern, mocking strategies with code examples, fixtures, priority-based "what to test" table |
| `docs/standards/code-review.md` | 146 | `standards/code-review.mdx` format — categorized checklist, per-area criteria, Conventional Comments with examples, approval criteria table |

6. **Updated satellite docs:**
   - `docs/contributing.md` — replaced stub with quick reference + links to all architecture docs + "Adding a New Engine" guide
   - `docs/configuration.md` — replaced stub with config reference, discovery mechanism, priority chain
   - Removed `docs/architecture/README.md` stub (replaced by `index.mdx`)

7. **No stack.yml changes needed** — paths already pointed to the right locations

## Key decisions

### What was adapted vs copied

The boilerplate is TypeScript/NestJS/monorepo. voiceCLI is Python/Typer/flat-CLI. Nothing was copy-pasted — every document was written from scratch using:

- **Boilerplate structure** as template (section headings, table formats, Mermaid diagram style, AI Quick Reference convention)
- **Actual voiceCLI code** as content source (real patterns found in the codebase, real code examples from `engine.py`, `api.py`, `translate.py`)

### What was NOT created

- **No DDD document** — voiceCLI doesn't need DDD. The domain is too simple (no aggregates, no cross-entity invariants, no database). Instead, `patterns.mdx` has a "When to adopt more structure" section with observable signals.
- **No ADRs** — left empty with pointer to `/adr` skill. ADRs should be created when actual decisions are made, not backfilled.
- **No frontend docs** — voiceCLI has no frontend.

### Format: `.mdx` vs `.md`

Used `.mdx` for architecture docs (to match boilerplate convention and support Mermaid rendering) and `.md` for standards (matching existing filenames referenced in `stack.yml`).

## What `/init` could do better

### Problem 1: Stubs are circular dead-ends

Current `/init` creates stubs like:
```markdown
# Backend Patterns
> Stub — see `CLAUDE.md` → Conventions section for authoritative rules.
```

These are worse than empty files because:
- They suggest the content exists elsewhere (it doesn't, in a structured form)
- Agents read them and get no actionable guidance
- They create a false sense of documentation coverage

**Suggestion:** Either generate real content from CLAUDE.md during init, or leave the files empty with a `TODO` marker that agents can detect and flag.

### Problem 2: No codebase analysis during init

`/init` creates the scaffolding but doesn't look at the actual code. The most valuable documentation — the pattern catalog, the layer diagram, the module responsibility table — can only be written by someone (or something) that has read the codebase.

**Suggestion:** After creating scaffolding, `/init` could run an analysis pass:
1. Read all source files in the `backend.path` from `stack.yml`
2. Identify ABC/interface patterns → document as "Strategy/Port" in architecture
3. Identify module dependency direction → generate layer diagram
4. Identify deferred imports → document as "Lazy Loading" pattern
5. Populate backend-patterns.md with actual module structure, not a stub

### Problem 3: No linkage between CLAUDE.md content and docs

CLAUDE.md often contains the real architecture documentation (pipeline description, capability matrix, conventions). But `/init` doesn't extract from it.

**Suggestion:** `/init` could parse CLAUDE.md sections and seed the docs:
- "Project Layout" → `docs/architecture/index.mdx` structure section
- "Key Patterns" / "Conventions" → `docs/standards/backend-patterns.md`
- Domain-specific terms → `docs/architecture/ubiquitous-language.mdx`
- Config reference → `docs/configuration.md`

### Problem 4: AI Quick Reference missing from stubs

The boilerplate's most agent-useful feature is the "AI Quick Reference" section at the bottom of each standard — compressed imperative rules that agents can quickly consume. The stubs don't even hint at this convention.

**Suggestion:** Include an empty "AI Quick Reference" section header in each stub template, with a comment explaining the format. This signals to whoever fills in the docs (human or agent) that this section is expected.

### Problem 5: No post-init validation

After `/init` creates stubs, there's no mechanism to flag that they need content. `/doctor` checks that paths exist but not that files have substance.

**Suggestion:** `/doctor` could check for stub markers (e.g., lines containing "Stub —") and warn:
```
warn: docs/standards/backend-patterns.md appears to be a stub (3 lines). Run /analyze or fill manually.
```

### Problem 6: Ubiquitous language not scaffolded

The boilerplate has `ubiquitous-language.mdx` but `/init` doesn't create it. Domain glossaries are valuable for all projects, not just complex ones.

**Suggestion:** Add `docs/architecture/ubiquitous-language.mdx` to the init scaffolding with a template:
```markdown
---
title: Ubiquitous Language
description: Glossary of domain terms and common confusions
---

## Glossary

| Term | Definition | Source |
|------|-----------|--------|

## Common Confusions

(Add entries as domain ambiguities are discovered.)
```

### Problem 7: Patterns doc not scaffolded

`/init` creates `docs/architecture/README.md` (a generic overview) but not a patterns doc. The patterns document turned out to be the most valuable piece — it's what the architect agent actually needs to make informed decisions.

**Suggestion:** Add `docs/architecture/patterns.mdx` to the scaffolding with section headers:
```markdown
## Design Patterns in Use
## SOLID Principles — Applied
## When to Adopt More Structure
```

## Summary of improvements for `/init`

| # | Improvement | Effort | Impact |
|---|-------------|--------|--------|
| 1 | Replace stub content with TODO markers agents can detect | Low | Medium — agents stop treating stubs as real docs |
| 2 | Run codebase analysis after scaffolding to seed real content | High | High — docs have actual value from day 1 |
| 3 | Extract from CLAUDE.md to seed docs | Medium | High — stops the circular reference problem |
| 4 | Add "AI Quick Reference" section header to all standard templates | Low | Medium — signals the expected format |
| 5 | Add stub detection to `/doctor` | Low | Medium — surfaces incomplete docs |
| 6 | Scaffold `ubiquitous-language.mdx` | Low | Low — template is easy to add |
| 7 | Scaffold `patterns.mdx` with section headers | Low | Medium — most valuable doc for architect agent |

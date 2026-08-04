# discovery-to-spec — Project Instructions

## What this is
A reference implementation of the three-layer agent architecture. It ingests a
raw discovery-call transcript and produces a structured technical project scope.
The value of this project is the CLEAN SEPARATION between the three layers. If a
change blurs the layers, it is wrong even if it "works."

## The three-layer rule (do not violate)

**Layer 1 — MCP tools (data access, zero judgment).**
Files: `src/d2s/mcp_server.py`.
- MCP tools move data in and out. That is all they do.
- MCP tools MUST NOT call the LLM, interpret content, score, classify, or make
  any judgment about the data. Moving a transcript is allowed; deciding what's
  important in it is not.
- If a tool is tempted to be "helpful" by analyzing what it moves, that is a
  layer violation. Stop and keep the judgment in the skill/subagent layer.

**Layer 2 — Agent Skill (all domain expertise).**
File: `.claude/skills/discovery-to-scope/SKILL.md`.
- The discovery-to-scope procedure lives here and ONLY here. Extraction rules,
  phasing logic, what to flag as an assumption, the output format — all of it.
- Do not hardcode domain judgment into Python. If the system needs to know
  "compliance constraints are must-haves," that rule belongs in the skill, not
  in a subagent's Python.
- This file is authored by Jeremy. Do not rewrite it. You may point out gaps.

**Layer 3 — Subagents (orchestration).**
File: `src/d2s/orchestrator.py`, defs in `.claude/agents/` if filesystem-based.
- Three specialists under one coordinator: Extractor, Gap-Analyst, Scope-Drafter.
- Subagents coordinate the work and apply the skill's rules. They call MCP tools
  for data and produce/consume ScopeRecord objects.
- Keep orchestration flat: coordinator → three specialists. Subagents do not
  spawn subagents.
- Remember: the only channel to a subagent is its prompt string; its context
  starts fresh. Pass paths and decisions explicitly.

## The schema is the contract
- `src/d2s/schema.py` defines `ScopeRecord` and its sub-models. This is the
  single shared vocabulary every layer speaks.
- Data crossing ANY layer boundary is a Pydantic model, never a loose dict.
- Need a new field? Add it to the schema deliberately. Do not pass untyped dicts
  to smuggle data across a boundary.

## Ownership boundary
Authored by Jeremy and never altered without discussion:
- `src/d2s/schema.py` (the ScopeRecord contract)
- `.claude/skills/discovery-to-scope/SKILL.md`
- `examples/` transcripts

Implemented by Claude Code against the decisions in this file: the MCP layer,
subagent contracts and orchestration, CLI, telemetry, Docker, tests. Anything
already committed follows the propose-before-altering rule: if you believe a
committed signature, contract, or decision needs to change, explain why and
propose it — do not change it unilaterally.

Filling in an implementation body from a signature Jeremy wrote is expected;
altering the signature is not.

## Schema conventions (established, extend, do not deviate)
- All enums inherit `(str, Enum)`.
- Every field carries `Field(description=...)` written for a model that sees
  that field and nothing else in the world. Enum-valued fields name every
  allowed value and give a tiebreaker for any ambiguous pair.
- Enum docstrings do not reach `model_json_schema()`. Shared descriptions live
  in module-level `_*_DESC` constants (see `_CONFIDENCE_DESC`).
- No field without a consumer. Every field is something an LLM must fill
  correctly. If nothing downstream reads it, it does not exist.
- `Confidence` routing is behavioral, not decorative: STATED passes through,
  IMPLIED generates a confirmation question in `open_questions`, ASSUMED is
  recorded in `assumptions[]` and blocks dependent phases from being sequenced
  as confirmed work. Downstream code must honor this.

## MCP layer decisions (settled)
- **Core/adapter split.** Typed synchronous core functions hold all logic and
  are tested directly. `@tool` adapters are thin async wrappers that unpack
  `args`, call the core, and serialize the result to a text content block. No
  logic in adapters.
- **Tool descriptions** live in module-level `_*_DESC` constants passed to the
  `@tool` decorator, written for the model, same standard as field descriptions.
- **Storage**: `.d2s/transcripts/` and `.d2s/records/`, resolved by a
  module-level resolver function, never a tool parameter. Tests monkeypatch the
  resolver. `.d2s/` is gitignored.
- **transcript_id**: filename slug plus first 8 hex chars of sha256 over
  normalized content, e.g. `riverstone-a3f9c1b2`. Re-ingestion is idempotent.
- **get_transcript** returns full text, not segments.
- **list_scope_records** returns `ScopeRecordSummary` objects, never full
  records.
- **Failures raise.** Exception messages are prompts the model reads. Write
  them as instructions: unknown transcript_id names `ingest_transcript` as the
  recovery path; missing path echoes the path tried. Operator errors
  (unwritable directory) may be terse.
- Tool names resolve as `mcp__<server>__<tool>` in `allowed_tools`.

## Schema additions required (delete this section once landed)
- `TranscriptMeta`: transcript_id, source_path, ingested_at. Description on
  source_path says provenance only, not a readable path, use `get_transcript`.
  No word_count, no separate content_hash.
- `ScopeRecord.transcript_id`: every record names the transcript its
  source_quotes point into.
- `ScopeRecordSummary`: record_id, transcript_id, one-line client_context,
  created_at, requirement count, open-question count.

## Telemetry (observability layer)
- Instrumentation uses `openinference-instrumentation-claude-agent-sdk` plus
  `openinference-instrumentation-anthropic`, exporting OTLP to Phoenix.
- All telemetry setup lives in ONE module: `src/d2s/telemetry.py`, exposing a
  single `init_telemetry()` called from the CLI entry point. No OTel imports
  anywhere else in `src/d2s/`.
- Telemetry is opt-in via environment: enabled only when
  `PHOENIX_COLLECTOR_ENDPOINT` is set. The pipeline must run identically, with
  identical output, when telemetry is off. Instrumentation observes; it never
  participates.
- MCP core functions, schema, and the skill contain zero telemetry code. Tool
  spans come from SDK hooks, not from hand-added spans inside tools.
- Traces may contain transcript content. The data policy below therefore
  applies to trace destinations: local Phoenix only for any non-synthetic run.

## Docker
- `Dockerfile` at repo root: multi-stage `uv` build, final image runs
  `python -m d2s`. `ANTHROPIC_API_KEY` arrives at runtime via environment,
  never baked into a layer or copied in a file.
- `docker-compose.yml` defines two services: `d2s` and `phoenix` (Arize
  Phoenix image), with `PHOENIX_COLLECTOR_ENDPOINT` pointing d2s at the
  phoenix service.
- `.dockerignore` excludes `.d2s/`, `.env`, `.git`, and anything gitignored.
- The claude-agent-sdk bundles the Claude Code CLI; the first Docker task is
  verifying that bundled runtime executes in the base image and documenting
  any system dependency it needs. Do not silently switch base images to make
  it work — surface the finding.

## Data policy (hard rule)
Real client transcripts must never be committed, referenced, or copied into
this repository, into `examples/`, into Docker images, or into any trace
destination other than a local Phoenix instance. `examples/` contains
synthetic transcripts only. If a file appears to contain real client,
employer, or personal data, stop and flag it instead of processing it.

## Conventions
- Python 3.12. Type hints on every function signature.
- `uv` for all dependency and run commands (`uv add`, `uv run`). Never pip.
- `ruff check` must pass clean before any commit.
- Every MCP tool gets a pytest test. No tool ships untested.
- Async where the SDK expects it (tools and subagent calls are async).

## SDK facts
- Subagents are defined via the `agents` parameter in `ClaudeAgentOptions`;
  routing keys off each agent's `description`.
- Include `"Agent"` in `allowed_tools` or subagent invocation will not
  auto-approve.
- `setting_sources=["project"]` is required or filesystem skills will not load.
- Subagents cannot spawn subagents. Orchestration stays flat.

## Backlog (scheduled, do not do opportunistically unless asked)
- `@model_validator(mode="after")` on `Constraint` and `Requirement`:
  `source_quote` required unless confidence is ASSUMED.
- `@model_validator` on `ScopeRecord`: `Phase.dependencies` must reference an
  existing `Phase.name`, with cycle detection.
- Empty-record detection (record with only client_context) is a Gap-Analyst
  check, not a schema change.

## How to work with me
- I review every diff before it merges. Produce changes I can read and explain.
- When you hit a real design decision, surface the tradeoff and let me choose —
  don't silently pick one.
- If something I wrote seems wrong, say so directly. I want the pushback.
- If you're unsure what I meant, ask rather than guessing and building the wrong
  thing.
- End every session by appending three lines to BUILD_LOG.md: what was built,
  what fought back, what was decided.
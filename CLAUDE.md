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

## The hand-draft boundary (Jeremy owns these; implement against them)
Hand-written by Jeremy and NOT to be redesigned without discussion:
- `src/d2s/schema.py` (the ScopeRecord contract)
- The four MCP tool signatures + docstrings in `mcp_server.py`
- The subagent contracts (each agent's input, output, tools, description)
- `.claude/skills/discovery-to-scope/SKILL.md`

You implement AGAINST these. If you believe one needs to change, explain why and
propose it — do not change it unilaterally. Filling in an implementation body
from a signature Jeremy wrote is expected; altering the signature is not.

## Conventions
- Python 3.12. Type hints on every function signature.
- `uv` for all dependency and run commands (`uv add`, `uv run`). Never pip.
- `ruff check` must pass clean before any commit.
- Every MCP tool gets a pytest test. No tool ships untested.
- Async where the SDK expects it (tools and subagent calls are async).

## How to work with me
- I review every diff before it merges. Produce changes I can read and explain.
- When you hit a real design decision, surface the tradeoff and let me choose —
  don't silently pick one.
- If something I wrote seems wrong, say so directly. I want the pushback.
- If you're unsure what I meant, ask rather than guessing and building the wrong
  thing.
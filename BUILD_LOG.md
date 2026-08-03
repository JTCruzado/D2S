## Day 1 — environment and skeleton (7/13)

*Backfilled 7/23. Written from memory and the commit, not logged same-day.*

**Did:** Installed the stack (Python 3.12, uv, ruff, pytest, claude-agent-sdk,
Claude Code). `uv init`, repo skeleton, first commit.

**Deferred:** Synthetic transcript #1. The plan had it on Day 1 and it slipped.

# BUILD_LOG

## Day 2 — schema.py (7/14)

**Did:** Hand-drafted `ScopeRecord` plus six supporting models and four enums.
Schema loads and `model_json_schema()` produces the full contract with all
nested models resolved.

**Fought back:** `ModuleNotFoundError: No module named 'd2s'` on first import.
`uv init` creates an application, not a package, so nothing put `src/d2s` on
the import path. Needed a `[build-system]` block with hatchling plus an
explicit `packages = ["src/d2s"]`, because the package name (`d2s`) differs
from the project name (`discovery-to-spec`).

Second one: commented out `source_quote` while fixing a syntax error and
nearly moved on without it. That field is what makes STATED distinguishable
from IMPLIED. Without it the confidence enum is just the model's self-report.

**Decided:**
- Dropped `Priority` as a single enum. It was trying to express two axes.
  Replaced with `effort` and `impact` on `Phase`, both using a shared
  `Magnitude` scale. The "lowest lift, greatest impact" phasing rule becomes
  computed ordering in the Scope-Drafter rather than a judgment the extractor
  has to make up front.
- `Confidence` is STATED / IMPLIED / ASSUMED, each with defined downstream
  routing. IMPLIED generates a confirmation question, ASSUMED gets recorded
  in assumptions[] and blocks dependent phases from being sequenced as
  confirmed work.
- `ConstraintKind` expanded from four to six. Added ORGANIZATIONAL and
  CONTRACTUAL because approval chains and vendor lock-in had nowhere to go.
- Enum docstrings do not reach `model_json_schema()`, only class docstrings
  do. So the Confidence routing rules live in a module-level
  `_CONFIDENCE_DESC` constant reused across every confidence field. This is
  the non-obvious thing I would not have found without printing the schema.

**Deferred:**
- `@model_validator(mode="after")` enforcing `source_quote` required unless
  confidence is ASSUMED. Currently stated in prose only. Day 10.
- `@model_validator` on `ScopeRecord`: `Phase.dependencies` must reference a
  real `Phase.name`. Cycle detection underneath it. Day 10.
- `ScopeRecord` requires only `client_context`, so a silently-failed
  extraction validates as an empty record. Probably a Gap-Analyst check
  rather than a schema change.
- ORGANIZATIONAL vs TECHNICAL tiebreaker is a guess. Transcript #1 tests it.
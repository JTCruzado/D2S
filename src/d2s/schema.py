"""ScopeRecord and supporting models.

The contract every layer speaks. The MCP layer moves these, the subagents
produce and consume them, the Scope-Drafter renders them into the final
scope document.

Design rules:
- Every fact carries a Confidence marker so the draft-not-contract framing
  holds all the way through the pipeline.
- Enums are closed sets. Field descriptions name every allowed value so an
  extractor never has to invent a category.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------


class ConstraintKind(str, Enum):
    """Category of limit placed on the solution space.

    Six closed categories. COMPLIANCE and CONTRACTUAL are the pair most
    often confused: compliance is imposed from outside by a regulator or
    standard, contractual is imposed by an agreement between two parties.
    """

    BUDGET = "budget"
    COMPLIANCE = "compliance"
    TIMELINE = "timeline"
    TECHNICAL = "technical"
    ORGANIZATIONAL = "organizational"
    CONTRACTUAL = "contractual"


class Confidence(str, Enum):
    """How the extractor came to believe a fact, and how the Gap-Analyst routes it.

    STATED   -- the client said it outright; passes through untouched.
    IMPLIED  -- inferred from what was said; generates a confirmation
                question in open_questions.
    ASSUMED  -- filled a gap the call never covered; recorded in
                assumptions[] and blocks any dependent phase from being
                sequenced as confirmed work.
    """

    STATED = "stated"
    IMPLIED = "implied"
    ASSUMED = "assumed"


class RequirementLevel(str, Enum):
    """Whether a requirement is load-bearing for the engagement.

    MUST_HAVE items gate delivery. NICE_TO_HAVE items are cut first when
    scope has to shrink.
    """

    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"


class Magnitude(str, Enum):
    """Shared three-point scale used for both effort and impact on a Phase."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Reused across every field that carries a Confidence value, so the routing
# rules reach the agent through the JSON schema rather than living only in
# the enum docstring, which model_json_schema() does not export.
_CONFIDENCE_DESC = (
    "How this fact was established. STATED: the client said it outright. "
    "IMPLIED: inferred from what was said but never stated directly. "
    "ASSUMED: filled a gap the call never covered. Use STATED only when a "
    "source_quote supports it."
)

# Reused wherever a transcript is named. Every layer identifies a transcript
# the same way, so the format and the recovery path are stated once.
_TRANSCRIPT_ID_DESC = (
    "Identifier of an ingested transcript, exactly as ingest_transcript "
    "returned it: a filename slug followed by a short content hash, for "
    "example 'riverstone-a3f9c1b2'. Pass it to get_transcript to read the "
    "transcript text. Never construct or guess this value."
)


# ---------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------


class Stakeholder(BaseModel):
    """A person or group whose needs or approval shape the engagement."""

    name: str = Field(
        description="Stakeholder's name as given in the transcript. Use their "
        "title alone if no name was said."
    )
    role: str = Field(
        description="Their function relative to this project, not their job "
        "title alone. Example: 'owns the vendor approval process'."
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="What this stakeholder is worried about or pushing for, "
        "one concern per item, in their own framing.",
    )
    confidence: Confidence = Field(
        description="How firmly this stakeholder's concerns are established. "
    )


class Constraint(BaseModel):
    """A limit on the solution space, with its source and how firmly it holds."""

    kind: ConstraintKind = Field(
        description=(
            "Category of the constraint. "
            "BUDGET: money limits. "
            "TIMELINE: date or duration limits. "
            "TECHNICAL: limits from existing systems, architecture, or "
            "in-house capability. "
            "COMPLIANCE: imposed externally by regulation or standard, such "
            "as SOC 2 or HIPAA. "
            "CONTRACTUAL: imposed by a private agreement between parties, "
            "such as vendor lock-in or an MSA clause. "
            "ORGANIZATIONAL: approval chains, staffing, internal politics. "
            "When a constraint is both a people problem and a technology "
            "problem, prefer ORGANIZATIONAL."
        )
    )
    description: str = Field(
        description="The constraint in one sentence, in the client's own framing."
    )
    source_quote: str | None = Field(
        default=None,
        description="Verbatim span from the transcript that establishes this "
        "constraint. Null only when confidence is ASSUMED.",
    )
    confidence: Confidence = Field(description=_CONFIDENCE_DESC)


class Requirement(BaseModel):
    """Something the delivered solution has to do, and how firmly it is established."""

    description: str = Field(
        description="What the solution must do, stated as an outcome rather "
        "than an implementation."
    )
    level: RequirementLevel = Field(
        description="MUST_HAVE if the engagement fails without it. "
        "NICE_TO_HAVE if it would be cut first under pressure."
    )
    source_quote: str | None = Field(
        default=None,
        description="Verbatim span from the transcript that establishes this "
        "requirement. Null only when confidence is ASSUMED.",
    )
    confidence: Confidence = Field(description=_CONFIDENCE_DESC)


class Phase(BaseModel):
    """A block of work that can be sequenced and delivered on its own."""

    name: str = Field(
        description="Short label for the phase. Must be unique within the "
        "record, since other phases reference it by name."
    )
    deliverables: list[str] = Field(
        description="What the client receives at the end of this phase, one "
        "per item. Concrete and checkable."
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Names of phases that must complete before this one can "
        "start. Must match a Phase.name in this record.",
    )
    effort: Magnitude = Field(
        description="Relative delivery cost of this phase compared to the "
        "others in this record, not an absolute estimate."
    )
    impact: Magnitude = Field(
        description="How much client-visible value this phase delivers, "
        "relative to the others in this record."
    )


class OpenQuestion(BaseModel):
    """Something the discovery call left unresolved that the client must answer."""

    question: str = Field(
        description="The question as it should be put to the client, in plain "
        "language they can answer without a technical background."
    )
    why_it_matters: str = Field(
        description="What in the scope changes depending on the answer. A "
        "question with no downstream consequence should not be here."
    )


class ExternalDependency(BaseModel):
    """Work or a decision outside both parties' direct control."""

    description: str = Field(
        description="What is depended on and what happens to the schedule if it slips."
    )
    owner: str = Field(
        description="The party responsible. Use 'unknown' when the call never "
        "established one, since an unowned dependency is a risk."
    )


# ---------------------------------------------------------------------
# Transcript provenance
# ---------------------------------------------------------------------


class TranscriptMeta(BaseModel):
    """What the system knows about an ingested transcript, minus its text.

    Returned by ingestion and listing so a caller can identify a transcript
    and decide whether to fetch it, without moving the transcript body.
    """

    transcript_id: str = Field(description=_TRANSCRIPT_ID_DESC)
    source_path: str = Field(
        description="Where this transcript came from when it was ingested. "
        "Provenance only: it records the file the operator supplied and is "
        "not a path you can read. To get the transcript text, call "
        "get_transcript with transcript_id."
    )
    ingested_at: datetime = Field(
        description="When this transcript was ingested, as a timezone-aware "
        "UTC timestamp. Recorded by the ingestion tool."
    )


# ---------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------


class ScopeRecord(BaseModel):
    """The complete structured scope derived from one discovery call.

    Produced by the Extractor, annotated by the Gap-Analyst, and rendered by
    the Scope-Drafter. This is the single contract shared across all three
    layers of the system.
    """

    transcript_id: str = Field(
        description=_TRANSCRIPT_ID_DESC + " Every source_quote in this record "
        "must be a verbatim span from that transcript and no other."
    )
    client_context: str = Field(
        description="What the client does, what prompted this engagement, and "
        "what they are trying to change. Two to four sentences."
    )
    stakeholders: list[Stakeholder] = Field(
        default_factory=list,
        description="Everyone named or referenced who shapes the outcome, "
        "including people not on the call.",
    )
    constraints: list[Constraint] = Field(
        default_factory=list,
        description="Every limit on the solution space surfaced by the call.",
    )
    requirements: list[Requirement] = Field(
        default_factory=list,
        description="Everything the solution has to do, stated and implied.",
    )
    phases: list[Phase] = Field(
        default_factory=list,
        description="The proposed sequence of work. Populated by the "
        "Scope-Drafter, left empty by the Extractor.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Gaps filled without client input, one per item. Every "
        "fact marked ASSUMED should appear here.",
    )
    open_questions: list[OpenQuestion] = Field(
        default_factory=list,
        description="What the client must answer before this draft becomes a "
        "commitment.",
    )
    dependencies_external: list[ExternalDependency] = Field(
        default_factory=list,
        description="Work or decisions outside both parties' control that can "
        "move the schedule.",
    )


class ScopeRecordSummary(BaseModel):
    """One stored ScopeRecord reduced to what a listing needs.

    Returned by list_scope_records so a caller can choose a record without
    loading every record's full contents.
    """

    record_id: str = Field(
        description="Identifier of the stored scope record. Use it to load "
        "the full record; this summary omits the record's contents."
    )
    transcript_id: str = Field(description=_TRANSCRIPT_ID_DESC)
    client_context: str = Field(
        description="Who the client is and what they want, in one line. "
        "Shortened from the record's full client_context for listing; load "
        "the record itself for the complete framing."
    )
    created_at: datetime = Field(
        description="When this scope record was created, as a timezone-aware "
        "UTC timestamp."
    )
    requirement_count: int = Field(
        description="How many requirements the full record holds, must_have "
        "and nice_to_have together."
    )
    open_question_count: int = Field(
        description="How many open questions the full record holds. A high "
        "count means the draft is far from a commitment."
    )

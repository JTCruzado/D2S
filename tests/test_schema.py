"""Tests for the schema additions: TranscriptMeta, ScopeRecord.transcript_id,
and ScopeRecordSummary.

These cover the contract itself, not the MCP layer that will populate it.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from d2s.schema import (
    Confidence,
    Requirement,
    RequirementLevel,
    ScopeRecord,
    ScopeRecordSummary,
    TranscriptMeta,
)

TRANSCRIPT_ID = "riverstone-a3f9c1b2"


def test_transcript_meta_constructs() -> None:
    meta = TranscriptMeta(
        transcript_id=TRANSCRIPT_ID,
        source_path="/Users/jeremy/calls/riverstone.txt",
        ingested_at=datetime(2026, 8, 4, 15, 30, tzinfo=UTC),
    )

    assert meta.transcript_id == TRANSCRIPT_ID
    assert meta.source_path == "/Users/jeremy/calls/riverstone.txt"
    assert meta.ingested_at == datetime(2026, 8, 4, 15, 30, tzinfo=UTC)


def test_scope_record_summary_constructs() -> None:
    summary = ScopeRecordSummary(
        record_id="rec-0001",
        transcript_id=TRANSCRIPT_ID,
        client_context="Regional insurer replacing a manual claims intake process.",
        created_at=datetime(2026, 8, 4, 16, 0, tzinfo=UTC),
        requirement_count=7,
        open_question_count=3,
    )

    assert summary.record_id == "rec-0001"
    assert summary.transcript_id == TRANSCRIPT_ID
    assert summary.requirement_count == 7
    assert summary.open_question_count == 3


def test_scope_record_requires_transcript_id() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ScopeRecord(client_context="A client with no transcript named.")

    missing = [
        error
        for error in excinfo.value.errors()
        if error["type"] == "missing" and error["loc"] == ("transcript_id",)
    ]
    assert missing, "transcript_id should be a required field on ScopeRecord"


def test_scope_record_accepts_transcript_id() -> None:
    record = ScopeRecord(
        transcript_id=TRANSCRIPT_ID,
        client_context="Regional insurer replacing a manual claims intake process.",
        requirements=[
            Requirement(
                description="Claims are acknowledged within one business day.",
                level=RequirementLevel.MUST_HAVE,
                source_quote="we have to get back to them same day, next day at worst",
                confidence=Confidence.STATED,
            )
        ],
    )

    assert record.transcript_id == TRANSCRIPT_ID
    assert record.requirements[0].confidence is Confidence.STATED


def test_scope_record_json_schema_resolves_with_transcript_id() -> None:
    schema = ScopeRecord.model_json_schema()

    assert "transcript_id" in schema["required"]

    field = schema["properties"]["transcript_id"]
    assert field["type"] == "string"
    assert field["description"].strip(), "transcript_id must carry a description"
    assert "ingest_transcript" in field["description"]
    assert "source_quote" in field["description"]

    # Every $ref the record emits must land on a definition that exists.
    definitions = schema.get("$defs", {})
    for prop in schema["properties"].values():
        ref = prop.get("$ref") or prop.get("items", {}).get("$ref")
        if ref is not None:
            assert ref.removeprefix("#/$defs/") in definitions

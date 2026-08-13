"""Tests for the MCP layer.

These exercise the synchronous core functions directly, with storage redirected
to tmp_path through the one seam the module exposes. Two tests reach an adapter,
where the behavior under test only exists at that level: the argument
descriptions the SDK builds into the tool schema, and the message a malformed
record produces.

Nothing here calls an LLM or starts a session.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.types import ListToolsRequest

from d2s import mcp_server
from d2s.schema import (
    Confidence,
    OpenQuestion,
    Requirement,
    RequirementLevel,
    ScopeRecord,
)

INGESTED_AT = datetime(2026, 8, 4, 15, 30, tzinfo=UTC)
CREATED_AT = datetime(2026, 8, 4, 16, 14, 7, tzinfo=UTC)

TRANSCRIPT_TEXT = (
    "Jeremy: Walk me through how claims come in today.\n"
    "\n"
    "Dana: It's email. Someone prints it, someone else keys it in.   \n"
    "We have to get back to them same day, next day at worst.\n"
)


@pytest.fixture(autouse=True)
def _storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "storage_root", lambda: tmp_path)


def _write_source(tmp_path: Path, name: str, text: str) -> Path:
    source = tmp_path / "incoming" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    with source.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return source


def _record(transcript_id: str) -> ScopeRecord:
    return ScopeRecord(
        transcript_id=transcript_id,
        client_context="Regional insurer replacing a manual claims intake process.",
        requirements=[
            Requirement(
                description="Claims are acknowledged within one business day.",
                level=RequirementLevel.MUST_HAVE,
                source_quote="we have to get back to them same day, next day at worst",
                confidence=Confidence.STATED,
            ),
            Requirement(
                description="Adjusters see intake volume by day.",
                level=RequirementLevel.NICE_TO_HAVE,
                confidence=Confidence.ASSUMED,
            ),
        ],
        open_questions=[
            OpenQuestion(
                question="Which team owns the intake mailbox today?",
                why_it_matters="It decides who has to change how they work.",
            )
        ],
    )


# ---------------------------------------------------------------------
# Ingestion and retrieval
# ---------------------------------------------------------------------


def test_ingest_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    source = _write_source(tmp_path, "riverstone.txt", TRANSCRIPT_TEXT)

    first = mcp_server.ingest_transcript(str(source), INGESTED_AT)
    second = mcp_server.ingest_transcript(
        str(source), INGESTED_AT + timedelta(days=1)
    )

    assert second.transcript_id == first.transcript_id
    # The second call must not restamp the record: a later ingested_at here
    # would mean re-ingestion silently rewrote history.
    assert second.ingested_at == first.ingested_at == INGESTED_AT
    assert len(list((tmp_path / "transcripts").glob("*.txt"))) == 1


def test_edited_content_yields_new_transcript_id(tmp_path: Path) -> None:
    source = _write_source(tmp_path, "riverstone.txt", TRANSCRIPT_TEXT)
    original = mcp_server.ingest_transcript(str(source), INGESTED_AT)

    edited_text = TRANSCRIPT_TEXT + "Dana: One more thing, we're SOC 2 bound.\n"
    _write_source(tmp_path, "riverstone.txt", edited_text)
    edited = mcp_server.ingest_transcript(str(source), INGESTED_AT)

    assert edited.transcript_id != original.transcript_id
    assert edited.transcript_id.startswith("riverstone-")
    # Both versions remain readable; editing adds, it does not replace.
    assert mcp_server.get_transcript(original.transcript_id) == TRANSCRIPT_TEXT
    assert mcp_server.get_transcript(edited.transcript_id) == edited_text


def test_get_transcript_returns_exact_full_text(tmp_path: Path) -> None:
    text = "First line with trailing space   \n\n\nLast line, no newline after"
    source = _write_source(tmp_path, "Riverstone Call 7-14.txt", text)

    meta = mcp_server.ingest_transcript(str(source), INGESTED_AT)

    assert mcp_server.get_transcript(meta.transcript_id) == text
    assert meta.transcript_id.startswith("riverstone-call-7-14-")


def test_unknown_transcript_id_raises_naming_ingest_transcript() -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        mcp_server.get_transcript("riverstone-deadbeef")

    message = str(excinfo.value)
    assert "ingest_transcript" in message
    assert "riverstone-deadbeef" in message


def test_missing_source_path_echoes_path(tmp_path: Path) -> None:
    missing = str(tmp_path / "nowhere" / "riverstone.txt")

    with pytest.raises(FileNotFoundError) as excinfo:
        mcp_server.ingest_transcript(missing, INGESTED_AT)

    assert missing in str(excinfo.value)


# ---------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------


def test_save_then_list_round_trips_summary(tmp_path: Path) -> None:
    record = _record("riverstone-a3f9c1b2")

    record_id = mcp_server.save_scope_record(record, CREATED_AT)
    assert record_id == "riverstone-a3f9c1b2-20260804T161407Z"

    # Listing reads only the sidecars, so removing the record body must not
    # change what comes back.
    (tmp_path / "records" / f"{record_id}.json").unlink()

    summaries = mcp_server.list_scope_records()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.record_id == record_id
    assert summary.transcript_id == "riverstone-a3f9c1b2"
    assert summary.client_context == record.client_context
    assert summary.created_at == CREATED_AT
    assert summary.requirement_count == 2
    assert summary.open_question_count == 1


def test_save_scope_record_refuses_to_overwrite(tmp_path: Path) -> None:
    record = _record("riverstone-a3f9c1b2")
    record_id = mcp_server.save_scope_record(record, CREATED_AT)
    stored = (tmp_path / "records" / f"{record_id}.json").read_bytes()

    with pytest.raises(FileExistsError) as excinfo:
        mcp_server.save_scope_record(
            _record("riverstone-a3f9c1b2").model_copy(
                update={"client_context": "A different client entirely."}
            ),
            CREATED_AT,
        )

    assert record_id in str(excinfo.value)
    assert (tmp_path / "records" / f"{record_id}.json").read_bytes() == stored


def test_list_scope_records_is_empty_before_any_save() -> None:
    assert mcp_server.list_scope_records() == []


# ---------------------------------------------------------------------
# Registration and adapters
# ---------------------------------------------------------------------


def _registered_schemas() -> dict[str, dict]:
    server = mcp_server.create_server()["instance"]
    result = asyncio.run(
        server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    )
    return {tool.name: tool.inputSchema for tool in result.root.tools}


def test_tools_are_registered() -> None:
    config = mcp_server.create_server()

    assert config["name"] == "d2s"
    assert {tool.name for tool in mcp_server.TOOLS} == {
        "ingest_transcript",
        "get_transcript",
        "save_scope_record",
        "list_scope_records",
    }
    assert all(tool.description.strip() for tool in mcp_server.TOOLS)


def test_argument_descriptions_reach_the_registered_schema() -> None:
    # The _*_DESC constants are worthless if the SDK drops them before the
    # model sees them, so assert against the built schema rather than the
    # SdkMcpTool object.
    schemas = _registered_schemas()

    transcript_id = schemas["get_transcript"]["properties"]["transcript_id"]
    assert "ingest_transcript" in transcript_id["description"]

    record = schemas["save_scope_record"]["properties"]["record"]
    assert "ScopeRecord" in record["description"]


def test_malformed_record_message_names_fields_and_recovery() -> None:
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            mcp_server._save_scope_record_tool.handler({"record": '{"foo": 1}'})
        )

    message = str(excinfo.value)
    # Pydantic's own text, naming the fields at fault.
    assert "transcript_id" in message
    assert "client_context" in message
    # Plus the recovery instruction, the one hand-written failure message here.
    assert "save_scope_record again" in message

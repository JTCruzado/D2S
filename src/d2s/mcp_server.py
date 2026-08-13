"""MCP tools for transcript and scope-record storage.

Layer 1 of the three-layer architecture. This module moves data in and out of
local storage and makes no judgment about it. Nothing here calls an LLM,
interprets a transcript, scores a record, or decides what matters — those
belong to the skill and the subagents.

Structure:
- Typed synchronous core functions hold all the logic and are what the tests
  exercise directly.
- ``@tool`` adapters are thin async wrappers that unpack ``args``, call the
  core, and serialize the result into one text content block.
- Storage locations come from ``storage_root()``, never from a tool parameter,
  so tests redirect them with a single monkeypatch.

Failures raise. Every message is written as an instruction, because the SDK
hands ``str(exception)`` back to the model as the tool result.
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from pydantic import ValidationError

from d2s.schema import (
    ScopeRecord,
    ScopeRecordSummary,
    TranscriptMeta,
    _TRANSCRIPT_ID_DESC,
)

# ---------------------------------------------------------------------
# Tool and argument descriptions
#
# Written for a model that sees this tool and nothing else in the world, the
# same standard the schema holds its Field descriptions to. Each one says what
# the tool returns, what it refuses to do, and how to recover.
# ---------------------------------------------------------------------


_INGEST_TRANSCRIPT_DESC = (
    "Store a discovery-call transcript file so the rest of the pipeline can "
    "read it. Takes the path to a text file and returns JSON with "
    "transcript_id, source_path, and ingested_at. Call this before any other "
    "tool: transcript_id is the only way to reference a transcript afterward. "
    "Ingestion is idempotent. Ingesting a file whose content has not changed "
    "returns the same transcript_id and the original ingested_at and stores "
    "nothing new; editing the file first produces a different transcript_id "
    "and leaves the earlier version intact. This tool stores the transcript "
    "exactly as given and never reads, summarizes, or interprets it."
)

_SOURCE_PATH_ARG_DESC = (
    "Filesystem path to the transcript file to ingest, as the operator gave "
    "it. Must be a readable UTF-8 text file. This path is recorded as "
    "provenance and is not how the transcript is read later; use the returned "
    "transcript_id for that."
)

_GET_TRANSCRIPT_DESC = (
    "Return the full text of a transcript that ingest_transcript has already "
    "stored. Returns the entire transcript verbatim: the whole document, not "
    "an excerpt, a summary, or the sections that look relevant. There is no "
    "search or segment option, so read the text and decide what matters "
    "yourself. Fails if the transcript_id is unknown."
)

_SAVE_SCOPE_RECORD_DESC = (
    "Persist a completed ScopeRecord and return the record_id it was stored "
    "under. The record is written exactly as supplied: this tool validates the "
    "JSON against the schema but never fills in, corrects, or reformats its "
    "contents. Saving is not idempotent. Each call stores a new record, and a "
    "save that would overwrite an existing record fails instead of replacing "
    "it. Call this once the record is final, not to checkpoint work in progress."
)

_RECORD_ARG_DESC = (
    "A complete ScopeRecord serialized as a JSON object string. Every required "
    "field must be present and every value must match the ScopeRecord schema, "
    "which defines what each field means and which values each enum allows. "
    "Partial records are rejected. When validation fails the error names the "
    "exact fields at fault; fix those and call save_scope_record again."
)

_LIST_SCOPE_RECORDS_DESC = (
    "List every stored scope record, oldest first, as a JSON array of "
    "summaries. Each summary carries record_id, transcript_id, "
    "client_context, created_at, requirement_count, and open_question_count: "
    "enough to choose a record without loading any record's full contents. "
    "Takes no arguments. Returns an empty array when nothing has been saved "
    "yet, which is not an error."
)


# ---------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------


def storage_root() -> Path:
    """Return the root of local d2s storage.

    The single seam for redirecting storage. Tests monkeypatch this function;
    no tool takes a storage location as a parameter.
    """
    return Path.cwd() / ".d2s"


def _transcripts_dir() -> Path:
    return storage_root() / "transcripts"


def _records_dir() -> Path:
    return storage_root() / "records"


def _read_text(path: Path) -> str:
    # newline="" keeps line endings exactly as stored, so a transcript comes
    # back byte-for-byte as it was ingested.
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------


_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def _slug(source_path: str) -> str:
    stem = Path(source_path).stem.lower()
    return _SLUG_UNSAFE.sub("-", stem).strip("-") or "transcript"


def _normalize(text: str) -> str:
    """Reduce text to the form the content hash is taken over.

    Line endings and trailing whitespace are presentation rather than content,
    so a file re-saved by a different editor keeps its transcript_id. Used only
    for hashing: what gets stored is always the text exactly as read.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:8]


def _require_utc(moment: datetime, name: str) -> datetime:
    """Reject naive datetimes rather than silently reading them as local time.

    Timestamps are passed in at write time, never defaulted, so that an
    idempotency violation shows up as a changed value instead of hiding behind
    a fresh one. A naive datetime would undermine that quietly.
    """
    if moment.tzinfo is None:
        raise ValueError(
            f"{name} must be a timezone-aware UTC datetime, but a naive one "
            f"was given. Pass datetime.now(UTC) rather than datetime.now()."
        )
    return moment.astimezone(UTC)


# ---------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------


def ingest_transcript(source_path: str, ingested_at: datetime) -> TranscriptMeta:
    """Store a transcript file and return its provenance.

    Idempotent on content: identical content yields the same transcript_id and
    returns the stored TranscriptMeta untouched, so ingested_at records the
    first ingestion rather than the most recent one.
    """
    ingested_at = _require_utc(ingested_at, "ingested_at")

    path = Path(source_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"No transcript file at {source_path}. Nothing was stored. Check "
            f"the path and call ingest_transcript again with the correct one."
        )

    text = _read_text(path)
    transcript_id = f"{_slug(source_path)}-{_content_hash(text)}"

    body = _transcripts_dir() / f"{transcript_id}.txt"
    sidecar = _transcripts_dir() / f"{transcript_id}.meta.json"
    if body.is_file() and sidecar.is_file():
        return TranscriptMeta.model_validate_json(_read_text(sidecar))

    meta = TranscriptMeta(
        transcript_id=transcript_id,
        source_path=str(path),
        ingested_at=ingested_at,
    )
    _write_text(body, text)
    _write_text(sidecar, meta.model_dump_json())
    return meta


def get_transcript(transcript_id: str) -> str:
    """Return the stored transcript text in full, exactly as ingested."""
    body = _transcripts_dir() / f"{transcript_id}.txt"
    if not body.is_file():
        raise FileNotFoundError(
            f"No transcript stored under transcript_id {transcript_id!r}. Call "
            f"ingest_transcript with the path to the transcript file and use "
            f"the transcript_id it returns. Never construct one yourself."
        )
    return _read_text(body)


def save_scope_record(record: ScopeRecord, created_at: datetime) -> str:
    """Write a scope record and its summary sidecar, and return the record_id.

    Both files are written here and nowhere else. The sidecar is derived data
    and never the source of truth: if the two ever disagree, the record wins.

    Not idempotent, deliberately. An existing record is never overwritten,
    because losing a stored record silently is worse than failing loudly.
    """
    created_at = _require_utc(created_at, "created_at")
    record_id = f"{record.transcript_id}-{created_at.strftime('%Y%m%dT%H%M%SZ')}"

    body = _records_dir() / f"{record_id}.json"
    if body.exists():
        raise FileExistsError(
            f"A scope record is already stored under record_id {record_id!r}, "
            f"and it was not overwritten. If that record is the one you meant, "
            f"it is already saved and there is nothing to do. If this is a "
            f"different record, call save_scope_record again in a moment so it "
            f"is stamped with a later time."
        )

    summary = ScopeRecordSummary(
        record_id=record_id,
        transcript_id=record.transcript_id,
        client_context=record.client_context,
        created_at=created_at,
        requirement_count=len(record.requirements),
        open_question_count=len(record.open_questions),
    )
    _write_text(body, record.model_dump_json())
    _write_text(_records_dir() / f"{record_id}.meta.json", summary.model_dump_json())
    return record_id


def list_scope_records() -> list[ScopeRecordSummary]:
    """Return a summary of every stored record, oldest first.

    Reads only the sidecars, never a record body. record_id ends in a UTC
    timestamp, so sorting by filename sorts chronologically.
    """
    records_dir = _records_dir()
    if not records_dir.is_dir():
        return []
    return [
        ScopeRecordSummary.model_validate_json(_read_text(sidecar))
        for sidecar in sorted(records_dir.glob("*.meta.json"))
    ]


# ---------------------------------------------------------------------
# Tool adapters
#
# Unpack args, call the core, serialize to one text block. No logic, and no
# error handling beyond turning a schema violation into a message the model can
# act on. The write-time timestamp is supplied here because the adapter is the
# MCP layer; the model is never asked to invent a clock.
# ---------------------------------------------------------------------


def _text(payload: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": payload}]}


@tool(
    "ingest_transcript",
    _INGEST_TRANSCRIPT_DESC,
    {"source_path": Annotated[str, _SOURCE_PATH_ARG_DESC]},
)
async def _ingest_transcript_tool(args: dict[str, Any]) -> dict[str, Any]:
    meta = ingest_transcript(args["source_path"], datetime.now(UTC))
    return _text(meta.model_dump_json())


@tool(
    "get_transcript",
    _GET_TRANSCRIPT_DESC,
    {"transcript_id": Annotated[str, _TRANSCRIPT_ID_DESC]},
)
async def _get_transcript_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _text(get_transcript(args["transcript_id"]))


@tool(
    "save_scope_record",
    _SAVE_SCOPE_RECORD_DESC,
    {"record": Annotated[str, _RECORD_ARG_DESC]},
)
async def _save_scope_record_tool(args: dict[str, Any]) -> dict[str, Any]:
    try:
        record = ScopeRecord.model_validate_json(args["record"])
    except ValidationError as error:
        raise ValueError(
            f"The record argument is not a valid ScopeRecord and nothing was "
            f"saved.\n{error}\nCorrect the fields named above and call "
            f"save_scope_record again with the complete record."
        ) from error
    return _text(save_scope_record(record, datetime.now(UTC)))


@tool("list_scope_records", _LIST_SCOPE_RECORDS_DESC, {})
async def _list_scope_records_tool(args: dict[str, Any]) -> dict[str, Any]:
    summaries = list_scope_records()
    return _text(f"[{','.join(summary.model_dump_json() for summary in summaries)}]")


TOOLS: list[SdkMcpTool[Any]] = [
    _ingest_transcript_tool,
    _get_transcript_tool,
    _save_scope_record_tool,
    _list_scope_records_tool,
]


def create_server() -> McpSdkServerConfig:
    """Build the in-process MCP server.

    The server name fixes how the tools resolve in ``allowed_tools``:
    ``mcp__d2s__ingest_transcript`` and so on.
    """
    return create_sdk_mcp_server(name="d2s", version="0.1.0", tools=TOOLS)

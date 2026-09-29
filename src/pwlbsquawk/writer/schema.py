"""JSON schema sent to the model as the structured-output format.

Kept to the subset structured outputs support (types, enums, required,
additionalProperties). Length limits are enforced after the call by pydantic.
"""

from __future__ import annotations

from ..models import Category

SQUAWK_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "ts": {
            "type": "string",
            "description": "ISO 8601 UTC timestamp of the triggering event, e.g. 2026-09-28T09:30:00Z",
        },
        "headline": {
            "type": "string",
            "description": "At most 140 characters. Present tense, UK English, your own words.",
        },
        "detail": {
            "type": "string",
            "description": "One or two factual sentences: source, figures, timing, reported reaction.",
        },
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "importance": {"type": "integer", "enum": [1, 2, 3]},
        "source_event_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ids of the input events this squawk is based on (at least one).",
        },
    },
    "required": ["ts", "headline", "detail", "category", "importance", "source_event_ids"],
    "additionalProperties": False,
}

WRITER_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "material": {
            "type": "boolean",
            "description": "false when nothing in the batch is a material fact for UK rates or gilts",
        },
        "squawks": {"type": "array", "items": SQUAWK_ITEM_SCHEMA},
    },
    "required": ["material", "squawks"],
    "additionalProperties": False,
}

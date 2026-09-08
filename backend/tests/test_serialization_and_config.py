"""Tests for JSON conversion, settings parsing and the SSE wire format."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from app.agent.events import DoneEvent, ToolCallEvent, format_sse
from app.config import Settings
from app.serialization import frame_to_records, jsonify


class TestJsonify:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (np.int64(3), 3),
            (np.float64(1.5), 1.5),
            (np.bool_(True), True),
            (float("nan"), None),
            (float("inf"), None),
            (pd.NaT, None),
            (pd.NA, None),
            (None, None),
            ("texte", "texte"),
        ],
    )
    def test_scalars(self, value: object, expected: object) -> None:
        assert jsonify(value) == expected

    def test_timestamps_become_iso_strings(self) -> None:
        assert jsonify(pd.Timestamp("2024-03-01")) == "2024-03-01T00:00:00"

    def test_containers_are_converted_recursively(self) -> None:
        converted = jsonify({"a": [np.int64(1), float("nan")], "b": np.array([2.0])})

        assert converted == {"a": [1, None], "b": [2.0]}

    def test_output_is_always_json_serialisable(self) -> None:
        frame = pd.DataFrame(
            {
                "when": pd.to_datetime(["2024-01-01", None]),
                "how_many": [1, np.nan],
                "what": ["a", None],
            }
        )

        records = frame_to_records(frame)
        encoded = json.dumps(records)

        assert "NaN" not in encoded
        assert records[1] == {"when": None, "how_many": None, "what": None}

    def test_nan_is_not_left_as_a_float(self) -> None:
        # json.dumps would happily emit the invalid literal `NaN`; we must not.
        assert jsonify(math.nan) is None


class TestSettings:
    def test_cors_origins_accept_a_comma_separated_string(self) -> None:
        settings = Settings(cors_origins="http://a.test, http://b.test ,")

        assert settings.cors_origins == ["http://a.test", "http://b.test"]

    def test_relative_data_dir_is_anchored_to_the_backend_package(self) -> None:
        settings = Settings(data_dir="data")

        assert settings.resolved_data_dir.is_absolute()
        assert settings.resolved_data_dir.name == "data"

    def test_upload_limit_is_exposed_in_bytes(self) -> None:
        assert Settings(max_upload_mb=2).max_upload_bytes == 2 * 1024 * 1024

    def test_guardrails_reject_absurd_values(self) -> None:
        with pytest.raises(ValueError, match="max_iterations"):
            Settings(max_iterations=0)


class TestSseFormat:
    def test_frame_carries_the_event_name_and_json_payload(self) -> None:
        frame = format_sse(DoneEvent(conversation_id="conv_1", message_id="msg_1"))

        assert frame.startswith("event: done\ndata: ")
        assert frame.endswith("\n\n")
        payload = json.loads(frame.split("data: ", 1)[1])
        assert payload["type"] == "done"
        assert payload["conversation_id"] == "conv_1"

    def test_accents_survive_the_wire_format(self) -> None:
        frame = format_sse(
            ToolCallEvent(id="c1", name="aggregate", arguments={"col": "région"}, iteration=1)
        )

        assert "région" in frame

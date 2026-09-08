"""HTTP API tests, including one that really consumes the SSE stream."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    """Parse a `text/event-stream` body into `(event_name, payload)` pairs."""
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if name and data:
            events.append((name, json.loads(data)))
    return events


class TestHealth:
    def test_reports_status_and_configuration(self, client: TestClient) -> None:
        body = client.get("/api/health").json()

        assert body["status"] == "ok"
        assert body["version"]
        assert body["llm_provider"] == "fake"
        assert body["dataset_count"] == 1
        assert body["tool_count"] == 9


class TestDatasets:
    def test_lists_the_bundled_dataset_and_starter_questions(self, client: TestClient) -> None:
        body = client.get("/api/datasets").json()

        assert [dataset["id"] for dataset in body["datasets"]] == ["tiny"]
        assert len(body["suggested_questions"]) >= 3

    def test_schema_exposes_columns_and_a_preview(self, client: TestClient) -> None:
        body = client.get("/api/datasets/tiny/schema").json()

        kinds = {column["name"]: column["kind"] for column in body["columns"]}
        assert kinds["order_date"] == "datetime"
        assert kinds["revenue"] == "numeric"
        assert len(body["preview"]) <= 10

    def test_unknown_dataset_returns_the_error_envelope(self, client: TestClient) -> None:
        response = client.get("/api/datasets/ghost/schema")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "dataset_not_found"
        assert "ghost" in response.json()["error"]["message"]

    def test_upload_registers_a_usable_dataset(self, client: TestClient) -> None:
        csv = b"city,people\nLyon,520000\nLille,235000\n"

        created = client.post("/api/datasets", files={"file": ("cities.csv", csv, "text/csv")})

        assert created.status_code == 201
        dataset_id = created.json()["id"]
        assert created.json()["row_count"] == 2

        schema = client.get(f"/api/datasets/{dataset_id}/schema").json()
        assert [column["name"] for column in schema["columns"]] == ["city", "people"]

    def test_non_csv_upload_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/datasets", files={"file": ("photo.png", b"\x89PNG", "image/png")}
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_dataset"

    def test_oversized_upload_is_refused(self, client: TestClient, settings) -> None:
        payload = b"a,b\n" + b"1,2\n" * (settings.max_upload_bytes // 4 + 1)

        response = client.post("/api/datasets", files={"file": ("huge.csv", payload, "text/csv")})

        assert response.status_code == 400
        assert "Mo" in response.json()["error"]["message"]


class TestChat:
    def test_runs_the_agent_and_returns_the_message(self, client: TestClient) -> None:
        response = client.post(
            "/api/chat",
            json={"message": "Quelle région performe le mieux ?", "dataset_id": "tiny"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["stopped_reason"] == "completed"
        assert body["message"]["role"] == "assistant"
        assert body["message"]["tool_invocations"][0]["name"] == "list_columns"
        assert body["conversation_id"].startswith("conv_")

    def test_the_conversation_can_be_replayed(self, client: TestClient) -> None:
        conversation_id = client.post(
            "/api/chat", json={"message": "Résume le dataset", "dataset_id": "tiny"}
        ).json()["conversation_id"]

        body = client.get(f"/api/conversations/{conversation_id}").json()

        assert body["dataset_id"] == "tiny"
        assert [message["role"] for message in body["messages"]] == ["user", "assistant"]

    def test_a_second_message_continues_the_same_conversation(self, client: TestClient) -> None:
        first = client.post(
            "/api/chat", json={"message": "Première question", "dataset_id": "tiny"}
        ).json()

        client.post(
            "/api/chat",
            json={
                "message": "Seconde question",
                "dataset_id": "tiny",
                "conversation_id": first["conversation_id"],
            },
        )

        body = client.get(f"/api/conversations/{first['conversation_id']}").json()
        assert len(body["messages"]) == 4

    def test_unknown_dataset_is_a_404(self, client: TestClient) -> None:
        response = client.post("/api/chat", json={"message": "Salut", "dataset_id": "ghost"})

        assert response.status_code == 404

    def test_unknown_conversation_is_a_404(self, client: TestClient) -> None:
        response = client.get("/api/conversations/conv_inconnue")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "conversation_not_found"

    def test_an_empty_message_is_rejected_with_a_readable_error(self, client: TestClient) -> None:
        response = client.post("/api/chat", json={"message": "", "dataset_id": "tiny"})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_request"
        assert "message" in error["detail"]


class TestChatStream:
    def test_events_arrive_in_the_expected_order(self, client: TestClient) -> None:
        with client.stream(
            "GET",
            "/api/chat/stream",
            params={"message": "Quelle région performe le mieux ?", "dataset_id": "tiny"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            body = "".join(response.iter_text())

        events = parse_sse(body)
        names = [name for name, _payload in events]

        assert names[0] == "status"
        assert names[-1] == "done"
        assert names.index("tool_call") < names.index("tool_result")
        assert "text_delta" in names
        assert "chart" in names

        # Every event's name matches the `type` inside its payload.
        assert all(name == payload["type"] for name, payload in events)

        done = events[-1][1]
        assert done["stopped_reason"] == "completed"
        assert done["conversation_id"].startswith("conv_")

    def test_tool_events_describe_what_ran(self, client: TestClient) -> None:
        with client.stream(
            "GET",
            "/api/chat/stream",
            params={"message": "Y a-t-il des valeurs aberrantes ?", "dataset_id": "tiny"},
        ) as response:
            body = "".join(response.iter_text())

        events = dict(parse_sse(body))
        assert events["tool_call"]["name"]
        assert events["tool_result"]["duration_ms"] >= 0
        assert events["tool_result"]["ok"] is True

    def test_the_stream_feeds_the_conversation_history(self, client: TestClient) -> None:
        with client.stream(
            "GET",
            "/api/chat/stream",
            params={"message": "Décris le dataset", "dataset_id": "tiny"},
        ) as response:
            body = "".join(response.iter_text())

        done = parse_sse(body)[-1][1]
        history = client.get(f"/api/conversations/{done['conversation_id']}").json()

        assert history["messages"][-1]["id"] == done["message_id"]

    def test_unknown_dataset_fails_before_the_stream_starts(self, client: TestClient) -> None:
        response = client.get(
            "/api/chat/stream", params={"message": "Salut", "dataset_id": "ghost"}
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "dataset_not_found"


class TestErrorEnvelope:
    def test_unknown_path_uses_the_same_shape(self, client: TestClient) -> None:
        response = client.get("/api/nope")

        assert response.status_code == 404
        assert set(response.json()["error"]) == {"code", "message", "detail"}

    def test_openapi_document_is_served(self, client: TestClient) -> None:
        body = client.get("/api/openapi.json").json()

        assert "/api/chat/stream" in body["paths"]
        assert "/api/datasets/{dataset_id}/schema" in body["paths"]

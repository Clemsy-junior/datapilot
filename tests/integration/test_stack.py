"""End-to-end tests: browser → nginx → backend.

They run inside a container on the Compose network and address the *frontend*
service, never the backend directly. That is the only way to prove what this
part of the project claims: that nginx serves the SPA, proxies `/api`, resolves
`backend` through Compose's internal DNS, and does not buffer the SSE stream.

Standard library only, on purpose: the runner is an unmodified `python:3.12-slim`
with the tests bind-mounted, so there is no image to build and no package to
download before the suite can start.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

# TODO(R11): these are HTTP-level tests. Real browser tests (Playwright)
# would additionally cover rendering, the EventSource client and the charts.
BASE_URL = os.environ.get("DATAPILOT_BASE_URL", "http://frontend").rstrip("/")
READY_TIMEOUT_SECONDS = 90.0


def _request(
    path: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes, dict[str, str]]:
    """Perform one HTTP call and return `(status, body, headers)`, errors included."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def _json(path: str, **kwargs: object) -> tuple[int, dict]:
    """Perform an HTTP call and decode the JSON body."""
    status, body, _headers = _request(path, **kwargs)  # type: ignore[arg-type]
    return status, json.loads(body.decode("utf-8"))


def _wait_until_ready() -> None:
    """Block until nginx answers /api/health, or fail the whole suite loudly."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last_error = "aucune tentative"
    while time.monotonic() < deadline:
        try:
            status, payload = _json("/api/health", timeout=5.0)
            if status == 200 and payload.get("status") == "ok":
                return
            last_error = f"HTTP {status}: {payload}"
        except (urllib.error.URLError, OSError, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(1.0)
    raise RuntimeError(f"{BASE_URL} n'a jamais répondu ({last_error})")


def setUpModule() -> None:
    """Wait for the stack before running anything."""
    _wait_until_ready()


class TestStaticServing(unittest.TestCase):
    """nginx serves the built single-page application."""

    def test_index_is_served(self) -> None:
        status, body, headers = _request("/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn(b'<div id="root">', body)

    def test_unknown_route_falls_back_to_the_spa(self) -> None:
        status, body, _headers = _request("/une/route/cliente")

        self.assertEqual(status, 200)
        self.assertIn(b'<div id="root">', body)

    def test_hashed_assets_are_cached_aggressively(self) -> None:
        _status, index, _headers = _request("/")
        text = index.decode("utf-8")
        start = text.index("/assets/")
        asset_path = text[start : text.index('"', start)]

        status, _body, headers = _request(asset_path)

        self.assertEqual(status, 200)
        self.assertIn("immutable", headers.get("Cache-Control", ""))


class TestApiThroughNginx(unittest.TestCase):
    """`/api` reaches the backend container by its Compose service name."""

    def test_health(self) -> None:
        status, payload = _json("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["tool_count"], 9)
        self.assertGreaterEqual(payload["dataset_count"], 1)

    def test_bundled_dataset_is_available(self) -> None:
        status, payload = _json("/api/datasets")

        self.assertEqual(status, 200)
        ids = [dataset["id"] for dataset in payload["datasets"]]
        self.assertIn("sales", ids)
        self.assertTrue(payload["suggested_questions"])

    def test_schema_describes_the_columns(self) -> None:
        status, payload = _json("/api/datasets/sales/schema")

        self.assertEqual(status, 200)
        kinds = {column["name"]: column["kind"] for column in payload["columns"]}
        self.assertEqual(kinds["order_date"], "datetime")
        self.assertEqual(kinds["revenue"], "numeric")
        self.assertLessEqual(len(payload["preview"]), 10)

    def test_error_envelope_survives_the_proxy(self) -> None:
        status, payload = _json("/api/datasets/inconnu/schema")

        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "dataset_not_found")


class TestAgentThroughNginx(unittest.TestCase):
    """A full agent turn, run through the proxy."""

    def test_chat_returns_tool_backed_numbers(self) -> None:
        body = json.dumps(
            {"message": "Quelle région performe le mieux ?", "dataset_id": "sales"}
        ).encode("utf-8")

        status, payload = _json(
            "/api/chat",
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=120.0,
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["stopped_reason"], "completed")
        message = payload["message"]
        self.assertEqual(message["role"], "assistant")
        self.assertTrue(message["content"].strip())

        names = [call["name"] for call in message["tool_invocations"]]
        self.assertIn("list_columns", names)
        self.assertTrue(all(call["ok"] for call in message["tool_invocations"]))

        # Every figure in the answer must exist in a tool result: check that the
        # winning region really is the one the aggregate returned.
        aggregate = next(
            call for call in message["tool_invocations"] if call["name"] == "aggregate"
        )
        top_region = aggregate["result"]["rows"][0]["region"]
        self.assertIn(top_region, message["content"])

    def test_conversation_history_is_readable(self) -> None:
        body = json.dumps({"message": "Décris le dataset", "dataset_id": "sales"}).encode("utf-8")
        _status, payload = _json(
            "/api/chat",
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=120.0,
        )

        status, history = _json(f"/api/conversations/{payload['conversation_id']}")

        self.assertEqual(status, 200)
        self.assertEqual([m["role"] for m in history["messages"]], ["user", "assistant"])


class TestServerSentEvents(unittest.TestCase):
    """The SSE stream survives the proxy, unbuffered."""

    def test_stream_delivers_events_in_order(self) -> None:
        query = urllib.parse.urlencode(
            {
                "message": "Montre-moi l'évolution du chiffre d'affaires par mois",
                "dataset_id": "sales",
            }
        )
        request = urllib.request.Request(f"{BASE_URL}/api/chat/stream?{query}")

        names: list[str] = []
        payloads: list[dict] = []
        with urllib.request.urlopen(request, timeout=120.0) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/event-stream", response.headers.get("Content-Type", ""))
            # nginx must not have re-buffered the response into a single body.
            self.assertNotIn("Content-Length", response.headers)

            current_name: str | None = None
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\n")
                if line.startswith("event: "):
                    current_name = line.removeprefix("event: ")
                elif line.startswith("data: ") and current_name is not None:
                    names.append(current_name)
                    payloads.append(json.loads(line.removeprefix("data: ")))
                    if current_name == "done":
                        break
                    current_name = None

        self.assertEqual(names[0], "status")
        self.assertEqual(names[-1], "done")
        self.assertIn("tool_call", names)
        self.assertIn("tool_result", names)
        self.assertIn("text_delta", names)
        self.assertLess(names.index("tool_call"), names.index("tool_result"))
        self.assertEqual(payloads[-1]["stopped_reason"], "completed")
        # The SSE event name and the `type` inside the payload must agree.
        self.assertEqual(names, [payload["type"] for payload in payloads])


class TestUpload(unittest.TestCase):
    """A CSV uploaded through nginx becomes queryable."""

    def test_upload_then_query(self) -> None:
        csv = b"ville,habitants\nLyon,520000\nLille,235000\nNantes,320000\n"
        boundary = "----datapilotboundary"
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="villes.csv"\r\n'
                "Content-Type: text/csv\r\n\r\n"
            ).encode()
            + csv
            + f"\r\n--{boundary}--\r\n".encode()
        )

        status, dataset = _json(
            "/api/datasets",
            method="POST",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

        self.assertEqual(status, 201)
        self.assertEqual(dataset["row_count"], 3)

        status, schema = _json(f"/api/datasets/{dataset['id']}/schema")
        self.assertEqual(status, 200)
        self.assertEqual([column["name"] for column in schema["columns"]], ["ville", "habitants"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

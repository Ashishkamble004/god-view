"""Local Google Chat mock receiver for L3 local-env approval card testing."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class ChatCardStore:
    """In-memory store of Google Chat cards received in the local environment."""

    def __init__(self) -> None:
        self._cards: list[dict[str, Any]] = []

    def receive_card(self, space: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or ("cardsV2" not in payload and "text" not in payload):
            raise ValueError("Payload must be a JSON object containing 'cardsV2' or 'text'")

        cards_v2 = payload.get("cardsV2") or []
        inferred_id = payload.get("approval_id")
        if not inferred_id and isinstance(cards_v2, list) and cards_v2:
            first_card = cards_v2[0]
            if isinstance(first_card, dict):
                inferred_id = first_card.get("cardId")

        entry: dict[str, Any] = {
            "space": space,
            "approval_id": inferred_id,
            "payload": payload,
            "received_at": datetime.now(tz=UTC).isoformat(),
        }
        self._cards.append(entry)
        return entry

    def list_cards(self) -> list[dict[str, Any]]:
        return list(self._cards)

    def clear(self) -> None:
        self._cards.clear()


STORE = ChatCardStore()


class ChatMockHandler(BaseHTTPRequestHandler):
    """HTTP handler for receiving Chat cards and forwarding interactive decisions."""

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok", "cards_received": len(STORE.list_cards())})
            return
        if self.path == "/messages":
            self._send_json(HTTPStatus.OK, {"messages": STORE.list_cards()})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown path: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        content_len = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON: {exc}"})
            return

        if self.path.startswith("/v1/spaces/") and self.path.endswith("/messages"):
            parts = self.path.strip("/").split("/")
            space = parts[2] if len(parts) >= 3 else "god-view"
            try:
                entry = STORE.receive_card(space=space, payload=payload)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, entry)
            return

        if self.path == "/callbacks/decision":
            approval_id = payload.get("approval_id")
            decision = payload.get("decision", "approve")
            principal = payload.get("principal", "user:local-operator@cymbal.local")
            if not approval_id or decision not in ("approve", "reject"):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "approval_id and decision ('approve'|'reject') are required"},
                )
                return
            sovereign_url = os.environ.get("SOVEREIGN_URL", "http://god-view-sovereign:8080").rstrip("/")
            target = f"{sovereign_url}/v1/approvals/{approval_id}:{decision}"
            req_body = json.dumps({"decided_by": principal, "channel": "chat"}).encode("utf-8")
            req = urllib.request.Request(
                target,
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Authenticated-User-Email": f"accounts.google.com:{principal}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    self._send_json(HTTPStatus.OK, {"forwarded": True, "sovereign_response": resp_data})
            except Exception as exc:  # Forwarding errors surfaced honestly with HTTP 502
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"Failed to forward callback: {exc}"})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown POST path: {self.path}"})


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), ChatMockHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()

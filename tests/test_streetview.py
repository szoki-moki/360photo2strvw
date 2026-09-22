from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.streetview import StreetViewClient, StreetViewError


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self.payload = payload
        self.text = ""
        self.reason = "fake response"

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        data = kwargs.get("data")
        if hasattr(data, "read"):
            kwargs["uploaded_bytes"] = data.read()
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_three_step_photo_upload_builds_expected_request(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg bytes")
    session = FakeSession(
        [
            FakeResponse(200, {"uploadUrl": "https://upload.example/photo"}),
            FakeResponse(200, {}),
            FakeResponse(
                200,
                {
                    "photoId": {"id": "photo-123"},
                    "mapsPublishStatus": "PUBLISHED",
                },
            ),
        ]
    )
    client = StreetViewClient(None, "api-key", session=session)  # type: ignore[arg-type]

    upload_url = client.start_upload()
    client.upload_photo_bytes(upload_url, photo)
    result = client.create_photo(
        upload_url,
        47.5,
        19.0,
        datetime(2026, 7, 9, 8, 1, 11, tzinfo=timezone.utc),
    )

    assert result["photoId"]["id"] == "photo-123"
    assert session.calls[0][2]["params"] == {"key": "api-key"}
    assert session.calls[1][2]["uploaded_bytes"] == b"jpeg bytes"
    body = session.calls[2][2]["json"]
    assert body["pose"]["latLngPair"] == {"latitude": 47.5, "longitude": 19.0}
    assert body["captureTime"] == "2026-07-09T08:01:11Z"


def test_retryable_publish_error_is_marked_ambiguous() -> None:
    session = FakeSession(
        [FakeResponse(503, {"error": {"message": "temporarily unavailable"}})]
    )
    client = StreetViewClient(None, "api-key", session=session)  # type: ignore[arg-type]

    with pytest.raises(StreetViewError) as caught:
        client.create_photo(
            "https://upload.example/photo",
            47.5,
            19.0,
            datetime.now(timezone.utc),
        )

    assert caught.value.ambiguous is True
    assert caught.value.retryable is True

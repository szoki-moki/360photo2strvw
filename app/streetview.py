from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from requests import Response
from requests.exceptions import RequestException

from app.metadata import rfc3339


BASE_URL = "https://streetviewpublish.googleapis.com/v1"


class StreetViewError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        status_code: int | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
    ):
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.retryable = retryable
        self.ambiguous = ambiguous


def _error_message(response: Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return str(error.get("message") or json.dumps(error, ensure_ascii=False))
        return str(error)
    except (ValueError, TypeError):
        body = response.text.strip()
        return body[:500] if body else response.reason


class StreetViewClient:
    def __init__(
        self,
        credentials: Credentials,
        api_key: str,
        timeout_seconds: int = 60,
        session: AuthorizedSession | None = None,
    ):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.session = session or AuthorizedSession(credentials)

    def close(self) -> None:
        self.session.close()

    def _check_response(self, response: Response, stage: str) -> None:
        if 200 <= response.status_code < 300:
            return
        retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
        raise StreetViewError(
            f"Google API returned HTTP {response.status_code} during {stage}: "
            f"{_error_message(response)}",
            stage=stage,
            status_code=response.status_code,
            retryable=retryable,
            ambiguous=stage == "publish" and retryable,
        )

    def start_upload(self) -> str:
        try:
            response = self.session.post(
                f"{BASE_URL}/photo:startUpload",
                params={"key": self.api_key},
                headers={"Content-Length": "0"},
                timeout=self.timeout_seconds,
            )
        except (RequestException, GoogleAuthError) as exc:
            raise StreetViewError(
                f"Could not request an upload URL: {exc}",
                stage="start upload",
                retryable=True,
            ) from exc
        self._check_response(response, "start upload")
        try:
            payload = response.json()
        except ValueError as exc:
            raise StreetViewError(
                "Google returned an invalid start-upload response.", stage="start upload"
            ) from exc
        upload_url = payload.get("uploadUrl")
        if not upload_url:
            raise StreetViewError(
                "Google did not return an upload URL.", stage="start upload"
            )
        return str(upload_url)

    def upload_photo_bytes(self, upload_url: str, path: Path) -> None:
        content_length = path.stat().st_size
        headers = {
            "Content-Type": "image/jpeg",
            "X-Goog-Upload-Protocol": "raw",
            "X-Goog-Upload-Content-Length": str(content_length),
        }
        try:
            with path.open("rb") as source:
                response = self.session.post(
                    upload_url,
                    data=source,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
        except (OSError, RequestException, GoogleAuthError) as exc:
            raise StreetViewError(
                f"Could not upload image bytes: {exc}",
                stage="upload bytes",
                retryable=True,
            ) from exc
        self._check_response(response, "upload bytes")

    def create_photo(
        self,
        upload_url: str,
        latitude: float,
        longitude: float,
        capture_time: datetime,
    ) -> dict[str, Any]:
        body = {
            "uploadReference": {"uploadUrl": upload_url},
            "pose": {
                "latLngPair": {
                    "latitude": latitude,
                    "longitude": longitude,
                }
            },
            "captureTime": rfc3339(capture_time),
        }
        try:
            response = self.session.post(
                f"{BASE_URL}/photo",
                params={"key": self.api_key},
                json=body,
                timeout=self.timeout_seconds,
            )
        except (RequestException, GoogleAuthError) as exc:
            raise StreetViewError(
                f"The publication result is unknown because the response was lost: {exc}",
                stage="publish",
                retryable=False,
                ambiguous=True,
            ) from exc
        self._check_response(response, "publish")
        try:
            return dict(response.json())
        except (ValueError, TypeError) as exc:
            raise StreetViewError(
                "The photo may have been published, but Google returned an invalid response.",
                stage="publish",
                ambiguous=True,
            ) from exc

    def get_photo(self, photo_id: str) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{BASE_URL}/photo/{quote(photo_id, safe='')}",
                params={"key": self.api_key},
                timeout=self.timeout_seconds,
            )
        except (RequestException, GoogleAuthError) as exc:
            raise StreetViewError(
                f"Could not retrieve photo status: {exc}",
                stage="status",
                retryable=True,
            ) from exc
        self._check_response(response, "status")
        try:
            return dict(response.json())
        except (ValueError, TypeError) as exc:
            raise StreetViewError(
                "Google returned an invalid photo-status response.", stage="status"
            ) from exc

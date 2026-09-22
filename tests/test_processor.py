from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.config import Settings
from app.processor import ImageBatchProcessor
from app.state import UploadState


SAMPLES = Path(__file__).resolve().parents[1] / "test_images"


class SuccessfulClient:
    def __init__(self):
        self.created: list[tuple[Any, ...]] = []

    def start_upload(self) -> str:
        return "https://upload.example/reference"

    def upload_photo_bytes(self, upload_url: str, path: Path) -> None:
        assert upload_url == "https://upload.example/reference"
        assert path.is_file()

    def create_photo(self, *args: Any) -> dict[str, Any]:
        self.created.append(args)
        return {
            "photoId": {"id": "photo-123"},
            "shareLink": "https://maps.example/photo-123",
            "mapsPublishStatus": "PUBLISHED",
        }


def _settings(image_dir: Path, data_dir: Path) -> Settings:
    return Settings(
        image_dir=image_dir,
        data_dir=data_dir,
        google_api_key="api-key",
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_refresh_token=None,
        default_timezone="Europe/Budapest",
        oauth_callback_port=8765,
        request_timeout_seconds=60,
        max_upload_attempts=3,
    )


def test_successful_upload_is_recorded_then_moved(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = image_dir / "GoProMaxGPS.jpg"
    shutil.copy2(SAMPLES / source.name, source)
    state = UploadState(tmp_path / "data" / "uploads.sqlite3")
    processor = ImageBatchProcessor(_settings(image_dir, tmp_path / "data"), state)
    client = SuccessfulClient()
    try:
        images = processor.analyze_images("Europe/Budapest")
        outcomes = processor.process_batch(images, client)  # type: ignore[arg-type]

        assert len(outcomes) == 1
        assert outcomes[0].status == "published"
        assert not source.exists()
        destination = image_dir / "uploaded" / source.name
        assert destination.is_file()
        record = state.get(images[0].sha256)
        assert record is not None
        assert record.photo_id == "photo-123"
        assert record.file_moved is True
        assert record.current_path == str(destination)
    finally:
        state.close()


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    uploaded_dir = image_dir / "uploaded"
    uploaded_dir.mkdir(parents=True)
    source = image_dir / "GSAA5035.JPG"
    shutil.copy2(SAMPLES / source.name, source)
    existing = uploaded_dir / source.name
    existing.write_bytes(b"existing file")
    state = UploadState(tmp_path / "data" / "uploads.sqlite3")
    processor = ImageBatchProcessor(_settings(image_dir, tmp_path / "data"), state)
    try:
        images = processor.analyze_images("Europe/Budapest")
        outcomes = processor.process_batch(images, SuccessfulClient())  # type: ignore[arg-type]

        assert outcomes[0].status == "published"
        assert existing.read_bytes() == b"existing file"
        assert outcomes[0].destination is not None
        assert "__" in outcomes[0].destination.name
        assert outcomes[0].destination.is_file()
    finally:
        state.close()

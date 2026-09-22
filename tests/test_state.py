from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.models import AnalyzedImage, ImageMetadata
from app.state import UploadState


def _analyzed(path: Path) -> AnalyzedImage:
    metadata = ImageMetadata(
        path=path,
        image_format="JPEG",
        width=4000,
        height=2000,
        size_bytes=100,
        latitude=47.5,
        longitude=19.0,
        capture_time=datetime(2026, 1, 1, 12, 0, 0),
        capture_time_source="DateTimeOriginal",
        xmp={},
    )
    return AnalyzedImage(
        path=path,
        sha256="abc123",
        metadata=metadata,
        capture_time_utc=datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
    )


def test_state_tracks_publication_before_file_move(tmp_path: Path) -> None:
    state = UploadState(tmp_path / "state.sqlite3")
    image = _analyzed(tmp_path / "image.jpg")
    destination = tmp_path / "uploaded" / "image.jpg"
    try:
        state.begin(image)
        state.mark_uploading(image.sha256, "https://upload.example")
        state.mark_publishing(image.sha256, "https://upload.example")
        state.mark_published(
            image.sha256,
            {
                "photoId": {"id": "photo-1"},
                "shareLink": "https://maps.example/photo-1",
                "mapsPublishStatus": "PUBLISHED",
            },
        )

        published = state.get(image.sha256)
        assert published is not None
        assert published.status == "published"
        assert published.photo_id == "photo-1"
        assert published.file_moved is False

        state.mark_moved(image.sha256, destination)
        moved = state.get(image.sha256)
        assert moved is not None
        assert moved.current_path == str(destination)
        assert moved.file_moved is True
    finally:
        state.close()

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ImageMetadata:
    path: Path
    image_format: str
    width: int
    height: int
    size_bytes: int
    latitude: float | None
    longitude: float | None
    capture_time: datetime | None
    capture_time_source: str | None
    xmp: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalyzedImage:
    path: Path
    sha256: str
    metadata: ImageMetadata | None
    capture_time_utc: datetime | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.metadata is not None and not self.errors


@dataclass(frozen=True)
class UploadOutcome:
    path: Path
    status: str
    message: str
    photo_id: str | None = None
    destination: Path | None = None

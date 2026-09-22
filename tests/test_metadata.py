from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pytest

from app.metadata import capture_time_as_utc, read_image_metadata, rfc3339
from app.validation import analyze_image


SAMPLES = Path(__file__).resolve().parents[1] / "test_images"


@pytest.mark.parametrize(
    ("filename", "latitude", "longitude", "date_text"),
    [
        ("GoProMaxGPS.jpg", 47.5291035, 19.0516367, "2025-10-17 09:12:46"),
        ("GSAA5035.JPG", 47.5398519, 19.0455023, "2026-07-09 10:01:11"),
    ],
)
def test_reads_supplied_photo_sphere_metadata(
    filename: str, latitude: float, longitude: float, date_text: str
) -> None:
    metadata = read_image_metadata(SAMPLES / filename)

    # The GoPro JPEGs contain a secondary 1440x720 preview in MPF metadata, so
    # Pillow correctly labels the JPEG-compatible container as MPO.
    assert metadata.image_format == "MPO"
    assert (metadata.width, metadata.height) == (5760, 2880)
    assert metadata.latitude == pytest.approx(latitude, abs=1e-7)
    assert metadata.longitude == pytest.approx(longitude, abs=1e-7)
    assert metadata.capture_time is not None
    assert metadata.capture_time.strftime("%Y-%m-%d %H:%M:%S") == date_text
    assert metadata.xmp["ProjectionType"] == "equirectangular"
    assert metadata.xmp["UsePanoramaViewer"] == "True"
    assert metadata.xmp["FullPanoWidthPixels"] == "5760"
    assert metadata.xmp["FullPanoHeightPixels"] == "2880"


@pytest.mark.parametrize("filename", ["GoProMaxGPS.jpg", "GSAA5035.JPG"])
def test_supplied_images_pass_preflight(filename: str) -> None:
    result = analyze_image(SAMPLES / filename, "Europe/Budapest")

    assert result.valid, result.errors
    assert result.capture_time_utc is not None
    assert result.capture_time_utc.tzinfo == timezone.utc
    assert result.warnings == (
        "JPEG contains an MPO secondary preview; the primary panorama will be uploaded unchanged.",
        "EXIF capture date has no UTC offset; the selected timezone will be used.",
    )


def test_budapest_daylight_saving_offset_is_applied() -> None:
    metadata = read_image_metadata(SAMPLES / "GSAA5035.JPG")
    assert metadata.capture_time is not None

    utc_value = capture_time_as_utc(metadata.capture_time, "Europe/Budapest")

    assert rfc3339(utc_value) == "2026-07-09T08:01:11Z"

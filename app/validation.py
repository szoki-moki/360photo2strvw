from __future__ import annotations

from pathlib import Path

from app.metadata import capture_time_as_utc, file_sha256, read_image_metadata
from app.models import AnalyzedImage, ImageMetadata


MAX_FILE_SIZE = 75 * 1024 * 1024
MIN_WIDTH = 3840
MIN_HEIGHT = 1920
REQUIRED_GPANO_FIELDS = {
    "ProjectionType",
    "UsePanoramaViewer",
    "PoseHeadingDegrees",
    "CroppedAreaImageWidthPixels",
    "CroppedAreaImageHeightPixels",
    "FullPanoWidthPixels",
    "FullPanoHeightPixels",
    "CroppedAreaLeftPixels",
    "CroppedAreaTopPixels",
}


def _xmp_integer(metadata: ImageMetadata, name: str) -> int | None:
    raw = metadata.xmp.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def validate_metadata(metadata: ImageMetadata) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if metadata.image_format.upper() not in {"JPEG", "MPO"}:
        errors.append(f"Unsupported image format {metadata.image_format}; JPEG is required.")
    elif metadata.image_format.upper() == "MPO":
        warnings.append(
            "JPEG contains an MPO secondary preview; the primary panorama will be "
            "uploaded unchanged."
        )
    if metadata.size_bytes > MAX_FILE_SIZE:
        errors.append("File is larger than the 75 MB Street View limit.")
    if metadata.width < MIN_WIDTH or metadata.height < MIN_HEIGHT:
        errors.append(
            f"Image is {metadata.width}x{metadata.height}; at least "
            f"{MIN_WIDTH}x{MIN_HEIGHT} is required."
        )
    if abs(metadata.width - 2 * metadata.height) > 2:
        errors.append("Image does not have the required 2:1 aspect ratio.")
    if metadata.latitude is None or metadata.longitude is None:
        errors.append("EXIF GPS latitude and longitude are required.")
    else:
        if not -90 <= metadata.latitude <= 90:
            errors.append("EXIF latitude is outside the valid range.")
        if not -180 <= metadata.longitude <= 180:
            errors.append("EXIF longitude is outside the valid range.")
    if metadata.capture_time is None:
        errors.append("An EXIF capture date is required.")

    missing_xmp = sorted(REQUIRED_GPANO_FIELDS - metadata.xmp.keys())
    if missing_xmp:
        errors.append("Missing Photo Sphere XMP fields: " + ", ".join(missing_xmp) + ".")
    if metadata.xmp.get("ProjectionType", "").lower() != "equirectangular":
        errors.append("GPano ProjectionType must be equirectangular.")
    if metadata.xmp.get("UsePanoramaViewer", "").lower() != "true":
        errors.append("GPano UsePanoramaViewer must be True.")

    cropped_width = _xmp_integer(metadata, "CroppedAreaImageWidthPixels")
    cropped_height = _xmp_integer(metadata, "CroppedAreaImageHeightPixels")
    full_width = _xmp_integer(metadata, "FullPanoWidthPixels")
    full_height = _xmp_integer(metadata, "FullPanoHeightPixels")
    left = _xmp_integer(metadata, "CroppedAreaLeftPixels")
    top = _xmp_integer(metadata, "CroppedAreaTopPixels")
    if cropped_width is not None and cropped_width != metadata.width:
        errors.append("GPano cropped width does not match the JPEG width.")
    if cropped_height is not None and cropped_height != metadata.height:
        errors.append("GPano cropped height does not match the JPEG height.")
    if full_width is not None and cropped_width is not None and cropped_width != full_width:
        errors.append("The panorama is not a full 360-degree horizontal image.")
    if full_height is not None and cropped_height is not None and cropped_height != full_height:
        errors.append("The panorama is vertically cropped rather than a full photo sphere.")
    if left not in {None, 0} or top not in {None, 0}:
        errors.append("The panorama's GPano crop origin must be at the top-left corner.")
    if full_width and full_height and abs(full_width - 2 * full_height) > 2:
        errors.append("GPano full panorama dimensions do not have a 2:1 aspect ratio.")

    heading = metadata.xmp.get("PoseHeadingDegrees")
    if heading is not None:
        try:
            heading_value = float(heading)
            if not 0 <= heading_value < 360:
                errors.append("GPano PoseHeadingDegrees must be in the range [0, 360).")
        except ValueError:
            errors.append("GPano PoseHeadingDegrees is not numeric.")

    if metadata.capture_time and metadata.capture_time.tzinfo is None:
        warnings.append("EXIF capture date has no UTC offset; the selected timezone will be used.")
    return errors, warnings


def analyze_image(path: Path, timezone_name: str) -> AnalyzedImage:
    try:
        digest = file_sha256(path)
    except OSError as exc:
        return AnalyzedImage(path, "", None, None, (f"Cannot read file: {exc}",))

    try:
        metadata = read_image_metadata(path)
    except Exception as exc:
        return AnalyzedImage(path, digest, None, None, (f"Cannot read image metadata: {exc}",))

    errors, warnings = validate_metadata(metadata)
    capture_time_utc = None
    if metadata.capture_time is not None:
        try:
            capture_time_utc = capture_time_as_utc(metadata.capture_time, timezone_name)
        except Exception as exc:
            errors.append(f"Cannot interpret the capture date: {exc}")
    return AnalyzedImage(
        path=path,
        sha256=digest,
        metadata=metadata,
        capture_time_utc=capture_time_utc,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )

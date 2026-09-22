from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

from PIL import Image

from app.models import ImageMetadata


GPS_IFD_TAG = 0x8825
EXIF_IFD_TAG = 0x8769
GPANO_NAMESPACE = "http://ns.google.com/photos/1.0/panorama/"
XMP_APP1_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return str(value).strip().strip("\x00") or None


def _number(value: Any) -> float:
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        denominator = float(value.denominator)
        if denominator == 0:
            raise ValueError("EXIF rational value has a zero denominator")
        return float(value.numerator) / denominator
    if isinstance(value, tuple) and len(value) == 2:
        denominator = float(value[1])
        if denominator == 0:
            raise ValueError("EXIF rational value has a zero denominator")
        return float(value[0]) / denominator
    return float(value)


def _coordinate(values: Any, reference: Any) -> float | None:
    if not values or len(values) < 3:
        return None
    degrees, minutes, seconds = (_number(value) for value in values[:3])
    result = degrees + minutes / 60.0 + seconds / 3600.0
    ref = (_text(reference) or "").upper()
    if ref in {"S", "W"}:
        result = -result
    elif ref not in {"N", "E"}:
        return None
    return result


def _parse_offset(value: Any) -> timezone | None:
    raw = _text(value)
    if not raw:
        return None
    if raw == "Z":
        return timezone.utc
    if len(raw) != 6 or raw[0] not in "+-" or raw[3] != ":":
        return None
    try:
        hours = int(raw[1:3])
        minutes = int(raw[4:6])
    except ValueError:
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    if raw[0] == "-":
        delta = -delta
    return timezone(delta)


def _parse_exif_datetime(value: Any, offset_value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    parsed: datetime | None = None
    for date_format in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, date_format)
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    offset = _parse_offset(offset_value)
    return parsed.replace(tzinfo=offset) if offset else parsed


def _gps_datetime(gps: dict[int, Any]) -> datetime | None:
    date_stamp = _text(gps.get(29))
    time_values = gps.get(7)
    if not date_stamp or not time_values or len(time_values) < 3:
        return None
    parsed_date = None
    for date_format in ("%Y:%m:%d", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(date_stamp, date_format).date()
            break
        except ValueError:
            continue
    if parsed_date is None:
        return None
    hours, minutes, seconds = (_number(value) for value in time_values[:3])
    whole_seconds = int(seconds)
    microseconds = int(round((seconds - whole_seconds) * 1_000_000))
    if microseconds == 1_000_000:
        whole_seconds += 1
        microseconds = 0
    parsed_time = time(int(hours), int(minutes), whole_seconds, microseconds)
    return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)


def _jpeg_app1_payloads(source: BinaryIO) -> list[bytes]:
    if source.read(2) != b"\xff\xd8":
        return []
    payloads: list[bytes] = []
    while True:
        prefix = source.read(1)
        if not prefix:
            break
        if prefix != b"\xff":
            continue
        marker = source.read(1)
        while marker == b"\xff":
            marker = source.read(1)
        if not marker or marker in {b"\xd9", b"\xda"}:
            break
        marker_number = marker[0]
        if marker_number in {0x01, *range(0xD0, 0xD8)}:
            continue
        raw_length = source.read(2)
        if len(raw_length) != 2:
            break
        length = int.from_bytes(raw_length, "big")
        if length < 2:
            break
        payload = source.read(length - 2)
        if len(payload) != length - 2:
            break
        if marker_number == 0xE1:
            payloads.append(payload)
    return payloads


def read_gpano_xmp(path: Path) -> dict[str, str]:
    properties: dict[str, str] = {}
    with path.open("rb") as source:
        payloads = _jpeg_app1_payloads(source)

    for payload in payloads:
        if payload.startswith(XMP_APP1_PREFIX):
            payload = payload[len(XMP_APP1_PREFIX) :]
        if b"ns.google.com/photos/1.0/panorama" not in payload:
            continue
        start = payload.find(b"<")
        if start < 0:
            continue
        try:
            root = ET.fromstring(payload[start:].rstrip(b"\x00"))
        except (ET.ParseError, ValueError):
            continue
        prefix = f"{{{GPANO_NAMESPACE}}}"
        for element in root.iter():
            if element.tag.startswith(prefix):
                value = (element.text or "").strip()
                if value:
                    properties[element.tag[len(prefix) :]] = value
            for name, value in element.attrib.items():
                if name.startswith(prefix):
                    properties[name[len(prefix) :]] = value.strip()
    return properties


def read_image_metadata(path: Path) -> ImageMetadata:
    size_bytes = path.stat().st_size
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format or "UNKNOWN"
        exif = image.getexif()
        try:
            gps = dict(exif.get_ifd(GPS_IFD_TAG))
        except (KeyError, TypeError, ValueError):
            gps = {}
        try:
            exif_ifd = dict(exif.get_ifd(EXIF_IFD_TAG))
        except (KeyError, TypeError, ValueError):
            exif_ifd = {}

        latitude = _coordinate(gps.get(2), gps.get(1))
        longitude = _coordinate(gps.get(4), gps.get(3))

        candidates = (
            ("DateTimeOriginal", exif_ifd.get(36867) or exif.get(36867), exif_ifd.get(36881)),
            ("DateTimeDigitized", exif_ifd.get(36868) or exif.get(36868), exif_ifd.get(36882)),
            ("DateTime", exif.get(306), exif_ifd.get(36880)),
        )
        capture_time = None
        capture_time_source = None
        for source_name, date_value, offset_value in candidates:
            capture_time = _parse_exif_datetime(date_value, offset_value)
            if capture_time is not None:
                capture_time_source = source_name
                break
        if capture_time is None:
            capture_time = _gps_datetime(gps)
            if capture_time is not None:
                capture_time_source = "GPSDateStamp/GPSTimeStamp"

    return ImageMetadata(
        path=path,
        image_format=image_format,
        width=width,
        height=height,
        size_bytes=size_bytes,
        latitude=latitude,
        longitude=longitude,
        capture_time=capture_time,
        capture_time_source=capture_time_source,
        xmp=read_gpano_xmp(path),
    )


def capture_time_as_utc(value: datetime, fallback_timezone: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(fallback_timezone))
    return value.astimezone(timezone.utc)


def rfc3339(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return normalized.replace("+00:00", "Z")

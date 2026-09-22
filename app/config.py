from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(ValueError):
    """Raised when environment configuration is invalid."""


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class Settings:
    image_dir: Path
    data_dir: Path
    google_api_key: str | None
    google_client_id: str | None
    google_client_secret: str | None
    google_refresh_token: str | None
    default_timezone: str
    oauth_callback_port: int
    request_timeout_seconds: int
    max_upload_attempts: int

    @classmethod
    def from_env(cls) -> "Settings":
        timezone_name = os.getenv("EXIF_TIMEZONE", "Europe/Budapest").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(
                f"EXIF_TIMEZONE is not a valid IANA timezone: {timezone_name!r}."
            ) from exc

        callback_port = _positive_int("OAUTH_CALLBACK_PORT", 8765)
        if callback_port > 65535:
            raise ConfigurationError("OAUTH_CALLBACK_PORT must be at most 65535.")

        return cls(
            image_dir=Path(os.getenv("IMAGE_UPLOAD_DIR", "/image_upload_dir")),
            data_dir=Path(os.getenv("APP_DATA_DIR", "/app/data")),
            google_api_key=os.getenv("GOOGLE_API_KEY") or None,
            google_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None,
            google_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or None,
            google_refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN") or None,
            default_timezone=timezone_name,
            oauth_callback_port=callback_port,
            request_timeout_seconds=_positive_int("REQUEST_TIMEOUT_SECONDS", 60),
            max_upload_attempts=_positive_int("MAX_UPLOAD_ATTEMPTS", 3),
        )

    def prepare_directories(self) -> None:
        if not self.image_dir.exists():
            raise ConfigurationError(
                f"Image directory does not exist: {self.image_dir}. "
                "Check IMAGE_UPLOAD_HOST_DIR in .env."
            )
        if not self.image_dir.is_dir():
            raise ConfigurationError(f"Image path is not a directory: {self.image_dir}.")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def require_api_key(self) -> str:
        if not self.google_api_key:
            raise ConfigurationError("GOOGLE_API_KEY is required for uploads.")
        return self.google_api_key

    def require_oauth_client(self) -> tuple[str, str]:
        if not self.google_client_id or not self.google_client_secret:
            raise ConfigurationError(
                "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are required "
                "for interactive authentication."
            )
        return self.google_client_id, self.google_client_secret

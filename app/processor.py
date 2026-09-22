from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable

from app.config import Settings
from app.models import AnalyzedImage, UploadOutcome
from app.state import UploadState
from app.streetview import StreetViewClient, StreetViewError
from app.validation import analyze_image


ProgressCallback = Callable[[str], None]
SUCCESSFUL_STATES = {"published", "move_failed"}


class ImageBatchProcessor:
    def __init__(self, settings: Settings, state: UploadState):
        self.settings = settings
        self.state = state

    def discover_images(self) -> list[Path]:
        paths = [
            path
            for path in self.settings.image_dir.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".jpg", ".jpeg"}
        ]
        return sorted(paths, key=lambda path: path.name.casefold())

    def analyze_images(self, timezone_name: str) -> list[AnalyzedImage]:
        images = [analyze_image(path, timezone_name) for path in self.discover_images()]
        return sorted(
            images,
            key=lambda image: (
                image.capture_time_utc is None,
                image.capture_time_utc,
                image.path.name.casefold(),
            ),
        )

    def candidate_images(
        self, images: Iterable[AnalyzedImage], *, retry_failed: bool = False
    ) -> list[AnalyzedImage]:
        candidates: list[AnalyzedImage] = []
        for image in images:
            if not image.valid:
                continue
            record = self.state.get(image.sha256)
            if retry_failed:
                if record is not None and record.status == "failed":
                    candidates.append(image)
                continue
            if record is None or record.status in {"pending", "uploading"}:
                candidates.append(image)
        return candidates

    def process_batch(
        self,
        images: Iterable[AnalyzedImage],
        client: StreetViewClient,
        *,
        retry_failed: bool = False,
        progress: ProgressCallback = print,
    ) -> list[UploadOutcome]:
        outcomes: list[UploadOutcome] = []
        for image in images:
            if not image.valid:
                outcomes.append(
                    UploadOutcome(image.path, "invalid", "; ".join(image.errors))
                )
                continue

            existing = self.state.get(image.sha256)
            if existing is not None:
                if existing.status in SUCCESSFUL_STATES:
                    outcomes.append(
                        UploadOutcome(
                            image.path,
                            "skipped",
                            f"Already published as {existing.photo_id or 'an unknown photo ID'}.",
                            existing.photo_id,
                        )
                    )
                    continue
                if existing.status in {"uncertain", "publishing"}:
                    if existing.status == "publishing":
                        self.state.mark_uncertain(
                            image.sha256,
                            "A previous run stopped while Google was publishing this photo.",
                        )
                    outcomes.append(
                        UploadOutcome(
                            image.path,
                            "uncertain",
                            "Previous publication result is uncertain; automatic retry is disabled.",
                        )
                    )
                    continue
                if retry_failed and existing.status != "failed":
                    continue
                if not retry_failed and existing.status == "failed":
                    outcomes.append(
                        UploadOutcome(
                            image.path,
                            "skipped",
                            "Previously failed; use the retry menu option.",
                        )
                    )
                    continue
            elif retry_failed:
                continue

            progress(f"\nUploading {image.path.name}...")
            try:
                outcome = self._process_one(image, client, progress)
            except KeyboardInterrupt:
                current = self.state.get(image.sha256)
                if current and current.status == "publishing":
                    self.state.mark_uncertain(
                        image.sha256,
                        "Interrupted while waiting for Google's publication response.",
                    )
                elif current and current.status in {"pending", "uploading"}:
                    self.state.mark_failed(image.sha256, "Upload interrupted by the user.")
                raise
            outcomes.append(outcome)
        return outcomes

    def _process_one(
        self,
        image: AnalyzedImage,
        client: StreetViewClient,
        progress: ProgressCallback,
    ) -> UploadOutcome:
        metadata = image.metadata
        if metadata is None or image.capture_time_utc is None:
            return UploadOutcome(image.path, "invalid", "Required metadata is unavailable.")
        if metadata.latitude is None or metadata.longitude is None:
            return UploadOutcome(image.path, "invalid", "GPS coordinates are unavailable.")

        self.state.begin(image)
        upload_url: str | None = None
        last_error: StreetViewError | None = None
        for attempt in range(1, self.settings.max_upload_attempts + 1):
            try:
                progress(
                    f"  Transferring bytes (attempt {attempt}/"
                    f"{self.settings.max_upload_attempts})..."
                )
                upload_url = client.start_upload()
                self.state.mark_uploading(image.sha256, upload_url)
                client.upload_photo_bytes(upload_url, image.path)
                last_error = None
                break
            except StreetViewError as exc:
                last_error = exc
                if not exc.retryable or attempt == self.settings.max_upload_attempts:
                    break
                delay = min(2 ** (attempt - 1), 8)
                progress(f"  Temporary error: {exc}. Retrying in {delay}s...")
                time.sleep(delay)

        if last_error is not None or upload_url is None:
            message = str(last_error or "Could not obtain an upload URL.")
            self.state.mark_failed(image.sha256, message)
            return UploadOutcome(image.path, "failed", message)

        self.state.mark_publishing(image.sha256, upload_url)
        progress("  Publishing photo metadata...")
        try:
            response = client.create_photo(
                upload_url,
                metadata.latitude,
                metadata.longitude,
                image.capture_time_utc,
            )
        except StreetViewError as exc:
            if exc.ambiguous:
                self.state.mark_uncertain(image.sha256, str(exc))
                return UploadOutcome(image.path, "uncertain", str(exc))
            self.state.mark_failed(image.sha256, str(exc))
            return UploadOutcome(image.path, "failed", str(exc))

        self.state.mark_published(image.sha256, response)
        photo_id_value = response.get("photoId") or {}
        photo_id = photo_id_value.get("id") if isinstance(photo_id_value, dict) else None
        try:
            destination = self._move_to_uploaded(image.path, image.sha256)
        except OSError as exc:
            message = f"Published successfully, but the source file could not be moved: {exc}"
            self.state.mark_move_failed(image.sha256, message)
            return UploadOutcome(image.path, "move_failed", message, photo_id)

        self.state.mark_moved(image.sha256, destination)
        return UploadOutcome(
            image.path,
            "published",
            "Published and moved to the uploaded directory.",
            photo_id,
            destination,
        )

    def _move_to_uploaded(self, source: Path, sha256: str) -> Path:
        upload_root = self.settings.image_dir.resolve()
        source_resolved = source.resolve()
        if source_resolved.parent != upload_root:
            raise OSError("Refusing to move a file outside the configured image directory")

        uploaded_dir = self.settings.image_dir / "uploaded"
        uploaded_dir.mkdir(parents=True, exist_ok=True)
        uploaded_resolved = uploaded_dir.resolve()
        if uploaded_resolved.parent != upload_root:
            raise OSError("The uploaded directory resolves outside the image directory")

        destination = uploaded_dir / source.name
        if destination.exists():
            destination = uploaded_dir / f"{source.stem}__{sha256[:10]}{source.suffix}"
        counter = 2
        while destination.exists():
            destination = uploaded_dir / (
                f"{source.stem}__{sha256[:10]}_{counter}{source.suffix}"
            )
            counter += 1
        source.rename(destination)
        return destination

    def retry_failed_moves(self, progress: ProgressCallback = print) -> int:
        moved = 0
        for record in self.state.move_failed_records():
            source = Path(record.source_path)
            if not source.exists():
                progress(f"Cannot retry move; source is missing: {source}")
                continue
            try:
                destination = self._move_to_uploaded(source, record.sha256)
            except OSError as exc:
                self.state.mark_move_failed(record.sha256, str(exc))
                progress(f"Move still failing for {source.name}: {exc}")
                continue
            self.state.mark_moved(record.sha256, destination)
            progress(f"Moved {source.name} to {destination}")
            moved += 1
        return moved

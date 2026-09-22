from __future__ import annotations

from pathlib import Path
from zoneinfo import available_timezones

from app.authentication import AuthenticationError, GoogleAuthenticator
from app.config import ConfigurationError, Settings
from app.models import AnalyzedImage, UploadOutcome
from app.processor import ImageBatchProcessor
from app.state import UploadState
from app.streetview import StreetViewClient, StreetViewError


class ApplicationMenu:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.timezone_name = settings.default_timezone
        self.state = UploadState(settings.data_dir / "uploads.sqlite3")
        self.authenticator = GoogleAuthenticator(settings)
        self.processor = ImageBatchProcessor(settings, self.state)

    def close(self) -> None:
        self.state.close()

    def run(self) -> None:
        while True:
            self._print_menu()
            try:
                choice = input("Choose an option: ").strip()
            except EOFError:
                choice = "8"
            try:
                if choice == "1":
                    self._analyze()
                elif choice == "2":
                    self._authenticate()
                elif choice == "3":
                    self._choose_timezone()
                elif choice == "4":
                    self._upload(retry_failed=False)
                elif choice == "5":
                    self._upload(retry_failed=True)
                elif choice == "6":
                    self._show_history()
                elif choice == "7":
                    self._refresh_statuses()
                elif choice in {"8", "q", "quit", "exit"}:
                    print("Goodbye.")
                    return
                else:
                    print("Unknown option. Enter a number from 1 to 8.")
            except KeyboardInterrupt:
                print("\nOperation interrupted; returning to the menu.")
            except (ConfigurationError, AuthenticationError, StreetViewError) as exc:
                print(f"\nError: {exc}")

    def _print_menu(self) -> None:
        print("\n=== 360 Photo to Google Street View ===")
        print(f"Image directory : {self.settings.image_dir}")
        print(f"EXIF timezone   : {self.timezone_name}")
        print(f"Google account  : {self.authenticator.credential_source()}")
        print("\n1. Analyze images (no upload)")
        print("2. Authenticate with Google")
        print("3. Choose EXIF timezone")
        print("4. Upload pending images")
        print("5. Retry failed uploads and file moves")
        print("6. Show upload history")
        print("7. Refresh Google publication statuses")
        print("8. Quit")

    def _analyze_images(self) -> list[AnalyzedImage]:
        print("\nReading image metadata...")
        return self.processor.analyze_images(self.timezone_name)

    def _analyze(self) -> None:
        images = self._analyze_images()
        if not images:
            print("No top-level JPEG images were found.")
            return
        valid_count = 0
        for image in images:
            metadata = image.metadata
            marker = "VALID" if image.valid else "INVALID"
            print(f"\n[{marker}] {image.path.name}")
            if metadata is not None:
                print(
                    f"  Size: {metadata.width}x{metadata.height}, "
                    f"{metadata.size_bytes / (1024 * 1024):.2f} MiB"
                )
                gps = (
                    f"{metadata.latitude:.7f}, {metadata.longitude:.7f}"
                    if metadata.latitude is not None and metadata.longitude is not None
                    else "missing"
                )
                print(f"  GPS: {gps}")
                capture = image.capture_time_utc.isoformat() if image.capture_time_utc else "missing"
                print(f"  Capture time (UTC): {capture}")
                print(f"  Projection: {metadata.xmp.get('ProjectionType', 'missing')}")
            for warning in image.warnings:
                print(f"  Warning: {warning}")
            for error in image.errors:
                print(f"  Error: {error}")
            valid_count += int(image.valid)
        print(f"\nSummary: {valid_count} valid, {len(images) - valid_count} invalid.")

    def _authenticate(self) -> None:
        print(
            "\nThe terminal will print a Google authorization URL. Open it in a browser "
            "on this computer; the callback is handled automatically."
        )
        self.authenticator.authenticate_interactively()
        print("Google authorization completed and saved.")

    def _choose_timezone(self) -> None:
        all_zones = sorted(available_timezones())
        common = [
            zone
            for zone in (
                "Europe/Budapest",
                "UTC",
                "Europe/London",
                "Europe/Berlin",
                "Europe/Paris",
                "America/New_York",
                "America/Los_Angeles",
                "Asia/Tokyo",
                "Australia/Sydney",
            )
            if zone in all_zones
        ]
        while True:
            query = input(
                "Search IANA timezones (blank shows common choices, 'cancel' returns): "
            ).strip()
            if query.casefold() in {"cancel", "q", "quit"}:
                return
            matches = common if not query else [
                zone for zone in all_zones if query.casefold() in zone.casefold()
            ]
            if not matches:
                print("No matching timezones.")
                continue
            if len(matches) > 40:
                print(f"{len(matches)} matches; enter a more specific search.")
                continue
            for index, zone in enumerate(matches, start=1):
                current = " (current)" if zone == self.timezone_name else ""
                print(f"{index:2}. {zone}{current}")
            selection = input("Select a number, or press Enter to search again: ").strip()
            if not selection:
                continue
            try:
                selected = matches[int(selection) - 1]
            except (ValueError, IndexError):
                print("Invalid selection.")
                continue
            self.timezone_name = selected
            print(f"EXIF timezone set to {selected} for this session.")
            return

    def _upload(self, *, retry_failed: bool) -> None:
        self.settings.require_api_key()
        images = self._analyze_images()
        candidates = self.processor.candidate_images(images, retry_failed=retry_failed)
        move_failures = self.state.move_failed_records() if retry_failed else []
        if not candidates and not move_failures:
            print("Nothing is eligible for this operation.")
            return

        if retry_failed and move_failures:
            print(f"{len(move_failures)} published file move(s) will also be retried.")
        if candidates:
            print(
                f"\n{len(candidates)} image(s) will be published publicly to Google Maps "
                f"using timezone {self.timezone_name}."
            )
            confirmation = input("Type UPLOAD to continue: ").strip()
            if confirmation != "UPLOAD":
                print("Upload cancelled.")
                return

        credentials = self.authenticator.get_credentials() if candidates else None
        client = None
        outcomes: list[UploadOutcome] = []
        try:
            if candidates and credentials is not None:
                client = StreetViewClient(
                    credentials,
                    self.settings.require_api_key(),
                    self.settings.request_timeout_seconds,
                )
                outcomes = self.processor.process_batch(
                    images,
                    client,
                    retry_failed=retry_failed,
                )
            if retry_failed:
                self.processor.retry_failed_moves()
        finally:
            if client is not None:
                client.close()
        self._print_outcomes(outcomes)

    @staticmethod
    def _print_outcomes(outcomes: list[UploadOutcome]) -> None:
        if not outcomes:
            return
        print("\nBatch results:")
        counts: dict[str, int] = {}
        for outcome in outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
            if outcome.status != "skipped":
                print(f"  {outcome.path.name}: {outcome.status} - {outcome.message}")
                if outcome.photo_id:
                    print(f"    Google photo ID: {outcome.photo_id}")
                if outcome.destination:
                    print(f"    Moved to: {outcome.destination}")
        print("  " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))

    def _show_history(self) -> None:
        records = self.state.list_recent()
        if not records:
            print("No upload history is recorded.")
            return
        print("\nRecent upload history:")
        for record in records:
            filename = Path(record.current_path).name
            remote = record.maps_publish_status or "unknown"
            photo = record.photo_id or "-"
            moved = "yes" if record.file_moved else "no"
            print(
                f"  {record.updated_at} | {record.status:11} | {filename} | "
                f"Google={remote} | moved={moved} | photo={photo}"
            )
            if record.share_link:
                print(f"    {record.share_link}")
            if record.error:
                print(f"    Error: {record.error}")

    def _refresh_statuses(self) -> None:
        records = self.state.published_records()
        if not records:
            print("No published photos with Google IDs are recorded.")
            return
        credentials = self.authenticator.get_credentials()
        client = StreetViewClient(
            credentials,
            self.settings.require_api_key(),
            self.settings.request_timeout_seconds,
        )
        try:
            for record in records:
                if not record.photo_id:
                    continue
                try:
                    response = client.get_photo(record.photo_id)
                except StreetViewError as exc:
                    print(f"  {record.photo_id}: {exc}")
                    continue
                self.state.update_remote_status(record.sha256, response)
                print(
                    f"  {record.photo_id}: "
                    f"{response.get('mapsPublishStatus', 'unknown')}"
                )
        finally:
            client.close()

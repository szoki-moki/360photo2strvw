from __future__ import annotations

import json
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import ConfigurationError, Settings


STREET_VIEW_SCOPE = "https://www.googleapis.com/auth/streetviewpublish"
SCOPES = [STREET_VIEW_SCOPE]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class AuthenticationError(RuntimeError):
    """Raised when Google user credentials cannot be obtained."""


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Credentials | None:
        if not self.exists:
            return None
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            return Credentials.from_authorized_user_info(info, SCOPES)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise AuthenticationError(f"Stored OAuth credentials are invalid: {exc}") from exc

    def save(self, credentials: Credentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(credentials.to_json(), encoding="utf-8")
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        os.replace(temporary_path, self.path)


class GoogleAuthenticator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token_store = TokenStore(settings.data_dir / "oauth-token.json")

    def credential_source(self) -> str:
        if self.token_store.exists:
            return "saved OAuth token"
        if self.settings.google_refresh_token:
            return "GOOGLE_REFRESH_TOKEN"
        return "not authenticated"

    def _from_environment(self) -> Credentials | None:
        if not self.settings.google_refresh_token:
            return None
        try:
            client_id, client_secret = self.settings.require_oauth_client()
        except ConfigurationError as exc:
            raise AuthenticationError(str(exc)) from exc
        return Credentials(
            token=None,
            refresh_token=self.settings.google_refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

    def get_credentials(self) -> Credentials:
        credentials = self.token_store.load() or self._from_environment()
        if credentials is None:
            raise AuthenticationError(
                "No Google user token is available. Choose 'Authenticate with Google' first."
            )
        if not credentials.valid:
            if not credentials.refresh_token:
                raise AuthenticationError("OAuth credentials have expired and cannot be refreshed.")
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                raise AuthenticationError(f"Google rejected the saved OAuth token: {exc}") from exc
            except Exception as exc:
                raise AuthenticationError(f"Could not refresh the Google access token: {exc}") from exc
        self.token_store.save(credentials)
        return credentials

    def authenticate_interactively(self) -> Credentials:
        try:
            client_id, client_secret = self.settings.require_oauth_client()
        except ConfigurationError as exc:
            raise AuthenticationError(str(exc)) from exc

        redirect_uri = f"http://127.0.0.1:{self.settings.oauth_callback_port}/"
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": TOKEN_URI,
                "redirect_uris": [redirect_uri],
            }
        }
        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=SCOPES,
            autogenerate_code_verifier=True,
        )
        try:
            credentials = flow.run_local_server(
                host="127.0.0.1",
                bind_addr="0.0.0.0",
                port=self.settings.oauth_callback_port,
                open_browser=False,
                authorization_prompt_message=(
                    "\nOpen this URL in a browser on the Docker host:\n\n{url}\n"
                ),
                success_message=(
                    "Google authorization completed. You may close this tab and return "
                    "to the terminal."
                ),
                access_type="offline",
                prompt="consent",
                timeout_seconds=300,
            )
        except OSError as exc:
            raise AuthenticationError(
                f"Cannot listen on OAuth callback port {self.settings.oauth_callback_port}: {exc}. "
                "Start the app with 'docker compose run --rm --service-ports app'."
            ) from exc
        except Exception as exc:
            raise AuthenticationError(f"Google OAuth authorization failed: {exc}") from exc
        self.token_store.save(credentials)
        return credentials

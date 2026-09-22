# 360 Photo to Google Street View

An interactive Python command-line application that reads geotagged JPEG photo
spheres, validates their EXIF and GPano metadata, and publishes them one at a
time with the Google Street View Publish API. It runs in Docker and stays at its
menu until you quit.

Successfully published files are moved to an `uploaded` directory inside the
configured image directory. Invalid, failed, and uncertain files remain where
they are.

## Prerequisites

- Docker with Docker Compose
- A Google account
- A Google Cloud project with the **Street View Publish API** enabled
- An API key from that project
- An OAuth 2.0 client with application type **Desktop app**

Complete the [Google Cloud and OAuth setup](#google-cloud-and-oauth-setup) at
the end of this README before creating `.env` or starting the application.

Uploads require authorization from the Google account that will own the
published imagery. An API key by itself is not sufficient.

If the OAuth consent screen is in testing mode, add the Google account that will
upload the photos as a test user.

## Configuration

Copy the example configuration:

```console
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and provide at least:

```dotenv
IMAGE_UPLOAD_HOST_DIR=C:/path/to/panoramas
GOOGLE_API_KEY=your-api-key
GOOGLE_OAUTH_CLIENT_ID=your-desktop-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-desktop-client-secret
EXIF_TIMEZONE=Europe/Budapest
```

The real `.env` is ignored by Git. `EXIF_TIMEZONE` is the default used for EXIF
timestamps that do not include an offset; it can be changed from a searchable
IANA timezone list while the application is running.

On Linux, set `HOST_UID` and `HOST_GID` to the results of `id -u` and `id -g`.
This lets the non-root container process create and move files in the bind
mount. The defaults are both `1000`, which is the usual first-user ID. Docker
Desktop on Windows does not normally require changing them.

`GOOGLE_REFRESH_TOKEN` is optional. It is intended for remote or fully headless
environments where the loopback browser flow cannot be used.

## Run

Build the image:

```console
docker compose build
```

Start the interactive application with its OAuth callback port enabled:

```console
docker compose run --rm --service-ports app
```

The first time, choose **Authenticate with Google**. The application prints a
URL. Open it in a browser on the Docker host, approve access, and return to the
terminal. Google redirects to `127.0.0.1:8765`; Docker forwards that one-time
callback to the application. There is no authorization code to paste into the
terminal.

OAuth credentials and upload history are stored in the Compose-managed
`app_data` volume, so they survive container replacement. Access tokens are
refreshed automatically.

## Processing behavior

Only top-level `.jpg` and `.jpeg` files in the configured directory are scanned.
The `uploaded` directory is therefore never scanned. Images are ordered by
capture time and then filename.

Before uploading, each file must have:

- JPEG format, no more than 75 MiB (JPEG-compatible MPO camera previews are
  accepted and uploaded unchanged)
- At least 3840 x 1920 pixels and a 2:1 aspect ratio
- EXIF GPS latitude and longitude
- An EXIF capture date
- Full equirectangular GPano/Photo Sphere XMP metadata

The application converts EXIF GPS coordinates to decimal WGS84 coordinates and
sends them explicitly with the capture time to `photo.create`. It does not alter
the JPEG bytes.

After Google confirms `photo.create`, the application first records the Google
photo ID in SQLite and then moves the source file to:

```text
<IMAGE_UPLOAD_HOST_DIR>/uploaded/<original-filename>
```

Existing files are never overwritten. A short content hash is appended when a
destination name already exists.

The content SHA-256 is the duplicate key. Already-published content is skipped.
Temporary failures while transferring bytes are retried, but an interrupted or
ambiguous final publication request is marked `uncertain` and is not retried
automatically, because doing so could publish a duplicate.

## Menu

1. Analyze images without uploading
2. Authenticate with Google
3. Choose the EXIF timezone for the current session
4. Upload pending images
5. Retry definite failures and post-publication file moves
6. Show local upload history
7. Refresh publication status from Google
8. Quit

Publishing requires typing `UPLOAD` after the batch summary. Photos published
through this API are public on Google Maps and are attributed to the authorized
Google account.

## Tests

Install the pinned dependencies and run:

```console
pytest
```

The tests use the included `test_images` files and mocked API sessions. They do
not publish anything to Google.

To run tests in the application image while mounting the repository on
PowerShell:

```powershell
docker build --tag 360photo2strvw:test .
docker run --rm --volume "${PWD}:/workspace:ro" --workdir /workspace 360photo2strvw:test python -m pytest -q -p no:cacheprovider
```

The normal runtime image deliberately contains only the application code; the
test command mounts the repository read-only.

## Google Cloud and OAuth setup

Complete these steps once before running the application. Google occasionally
changes the Cloud Console layout, but the section names below match the current
Google Auth Platform interface.

### 1. Create or select a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Use the project selector in the top bar.
3. Select an existing project or choose **New project**.
4. Give it a recognizable name, such as `360 Photo Street View Uploader`, and
   create it.
5. Make sure this project remains selected for every following step. API keys,
   OAuth clients, consent configuration, and enabled APIs must all belong to
   the same project.

### 2. Enable the Street View Publish API

1. In the Cloud Console, go to **APIs & Services > Library**.
2. Search for **Street View Publish API**.
3. Open it and click **Enable**.
4. Go to **APIs & Services > Enabled APIs & services** and confirm that
   **Street View Publish API** appears in the list.

The API can also be opened directly from the
[Street View Publish API library page](https://console.cloud.google.com/apis/library/streetviewpublish.googleapis.com).
Google's own [Street View prerequisites](https://developers.google.com/streetview/publish/prereqs)
also describe the project, API, and credential requirements.

### 3. Configure the Google Auth Platform

1. Open **Google Auth Platform > Overview** in the selected project.
2. If prompted, click **Get started**.
3. Under **App Information**, enter:
   - an app name, such as `360 Photo Uploader`;
   - a user support email.
4. Under **Audience**, choose:
   - **Internal** only when the project belongs to a Google Workspace
     organization and every uploading account is in that organization; or
   - **External** for a personal Gmail account or accounts outside the
     organization.
5. Enter a monitored developer contact email and finish the initial setup.

These values are shown during Google authorization. Google's
[Google Auth Platform guide](https://support.google.com/cloud/answer/15544987)
explains the Branding, Audience, Data Access, and Clients sections.

### 4. Add the Street View OAuth scope

1. Go to **Google Auth Platform > Data Access**.
2. Choose **Add or remove scopes**.
3. Find the Street View Publish scope, or add it manually if the interface
   offers that option:

   ```text
   https://www.googleapis.com/auth/streetviewpublish
   ```

4. Save the Data Access configuration.

This is the API's read/write scope. The application requests only this scope.
Every Street View Publish request must be authorized by a Google user; a
service account is not a replacement for this OAuth flow. See Google's
[Street View authorization documentation](https://developers.google.com/streetview/publish/authorizing).

### 5. Configure the audience and test user

For an **External** app that is still in **Testing**:

1. Go to **Google Auth Platform > Audience**.
2. Under **Test users**, click **Add users**.
3. Add the exact Google account that will own and upload the Street View
   imagery.
4. Save the changes.

Testing mode is sufficient for initial use, but Google limits it to listed test
users. Because this application requests offline access, a refresh token issued
to an External app in Testing normally expires after seven days. Re-run the
menu's **Authenticate with Google** action when that happens.

For longer-lived or multi-user use, review **Audience > Publishing status** and
Google's verification requirements before changing the app to **In
production**. Public External apps may need branding and scope verification.
Google documents the differences between Testing and In production in
[Manage App Audience](https://support.google.com/cloud/answer/15549945).

### 6. Create the Desktop OAuth client

1. Go to **Google Auth Platform > Clients**.
2. Click **Create client**.
3. Set **Application type** to **Desktop app**.
4. Enter a name such as `360 Photo Uploader Desktop`.
5. Click **Create**.
6. Copy the displayed **Client ID** and **Client secret** immediately and store
   them securely.

Do not create a Web application client and do not add a custom redirect URI.
Desktop clients support the loopback redirect used here. During authentication,
Google redirects the host browser to `http://127.0.0.1:8765/`, and Docker
forwards that request to the temporary listener in the container. Google's
[Desktop OAuth documentation](https://developers.google.com/identity/protocols/oauth2/native-app)
describes this loopback flow.

Place the copied values in `.env`:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
```

### 7. Create and restrict an API key

1. Go to **APIs & Services > Credentials**.
2. Click **Create credentials > API key**.
3. Edit the new key.
4. Under **API restrictions**, select **Restrict key** and allow only
   **Street View Publish API**.
5. Under **Application restrictions**:
   - use an IP-address restriction only if the computer or network has a stable
     public outbound IP address; or
   - leave application restrictions unset for local use when the outbound IP
     changes. `localhost` and private IP addresses cannot be used as an IP
     restriction.
6. Save the key and copy its value.

Restricting the key to the Street View Publish API prevents it from being used
with unrelated Google APIs. See Google's
[API key restriction guide](https://docs.cloud.google.com/docs/authentication/api-keys#api_key_restrictions).

Place the key in `.env`:

```dotenv
GOOGLE_API_KEY=your-api-key
```

Never commit `.env`, an OAuth client secret, an API key, or a refresh token.

### 8. Verify the setup and authorize the uploading account

Before the first upload, confirm that:

- the correct Cloud project is selected;
- Street View Publish API is enabled;
- the OAuth scope is present under Data Access;
- the OAuth client type is Desktop app;
- the uploading Google account is an allowed test user when the app is in
  Testing;
- all three credential values are present in `.env`.

Start the application:

```console
docker compose run --rm --service-ports app
```

Choose **Authenticate with Google**, open the printed URL, and sign in with the
Google account that should own the public Street View photos. Review and approve
the requested Street View permission. The application saves the resulting
refresh token in the private Compose `app_data` volume; it does not write the
token to the image directory or repository.

After authorization, choose **Analyze images** before the first upload. If that
succeeds, use **Upload pending images** and review the public-upload confirmation
carefully.

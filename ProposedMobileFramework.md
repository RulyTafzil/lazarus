# Mobile framework specification for Lazarus

This document defines the requirements, architecture, and implementation plans for extending Lazarus email access to mobile devices. It evaluates two approaches: a mobile web application and a dedicated native mobile application. Both share a common host API service running on the user's primary workstation over Tailscale.

## Core requirements

Any mobile implementation must satisfy the following functional requirements:

1. **Email viewing and search**
   - Default to an inbox search view equivalent to `tag:inbox`.
   - Accept arbitrary notmuch search queries entered by the user, such as `tag:unread`, `from:alice`, or date ranges.
   - Display full conversation threads with collapsible messages.
   - Render HTML emails safely without breaking mobile viewports.

2. **Tagging and triage**
   - Apply single-tap or swipe actions for common operations: archive (`-inbox -unread`), trash (`+trash`), mark unread (`+unread`), and mark flagged (`+flagged`).
   - Provide a tag editor to add or remove arbitrary notmuch tags on a thread or specific message.
   - Execute file moves to Maildir folders when trashing or archiving if configured in Lazarus.

3. **Replying with attachments**
   - Support plaintext email composition for replies and forwards.
   - Auto-populate In-Reply-To, References, To, Cc, and Subject headers from the parent message.
   - Allow attaching files from the mobile device storage, photo library, or camera.
   - Dispatch outgoing messages through the host's configured sendmail command (`msmtp`).

4. **Address autocomplete**
   - Suggest recipient email addresses dynamically as the user types in To, Cc, or Bcc fields.
   - Mirror the behavior in [address_completer.py](file:///home/rulyt/Projects/lazarus/lazarus/address_completer.py) by querying the host's notmuch address index with substring matching.

5. **Local storage expectations**
   - Offline storage on the mobile device is optional. A live network client operating over Tailscale satisfies the core triage need.
   - The architecture must allow adding an offline cache layer later without redesigning the backend.

## Shared backend architecture

Both mobile options depend on a lightweight, headless API daemon running on the Linux host. The daemon exposes Lazarus and notmuch operations through HTTP endpoints.

### Network and security

- **Interface binding:** The server binds strictly to the Tailscale interface (`100.x.y.z`) or listens on `127.0.0.1` behind a reverse proxy bound to Tailscale. It never listens on public interfaces.
- **Transport security:** Tailscale encrypts all traffic point-to-point via WireGuard.
- **Authentication:** Requests require an `Authorization: Bearer <token>` header matching a secret stored in the user's Lazarus configuration.
- **Process management:** Runs as a standard systemd user service (`systemd --user`) so it stays active when the Lazarus desktop GUI is closed.

### Reused Lazarus modules

The daemon avoids duplicating logic by importing existing pure Python modules from the Lazarus package:

- [notmuch.py](file:///home/rulyt/Projects/lazarus/lazarus/notmuch.py): Provides CLI wrappers for `search_json`, `count_batch`, `tags`, and `tag`.
- [mail_utils.py](file:///home/rulyt/Projects/lazarus/lazarus/mail_utils.py): Handles message part decomposition, body extraction, and quoting logic.
- [mime_builder.py](file:///home/rulyt/Projects/lazarus/lazarus/mime_builder.py): Assembles RFC-compliant multipart MIME messages with attachments.
- [rules.py](file:///home/rulyt/Projects/lazarus/lazarus/rules.py): Applies automated tag and folder rules if triggered remotely.

### API specification

The host service implements the following endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/search` | Query notmuch threads. Accepts `q` query string and `limit`/`offset`. Returns thread metadata array. |
| `GET` | `/api/thread/{id}` | Fetch all messages in a thread using `notmuch show --format=json --include-html`. |
| `GET` | `/api/message/{id}/part/{part_id}` | Download raw body or attachment data. |
| `POST` | `/api/tag` | Modify tags for a thread or message ID. Payload: `{"ids": [...], "add": [...], "remove": [...]}`. |
| `POST` | `/api/move` | Enqueue Maildir moves for archive or trash actions. |
| `GET` | `/api/addresses` | Return matching contacts for query string `q` via `notmuch address`. |
| `POST` | `/api/send` | Multipart form submission containing recipient headers, plain body, and attached binary files. Pipes to `msmtp`. |

## Option 1: Mobile web application (PWA)

A responsive single-page or server-rendered web application served directly by the host daemon.

### Technical design

- **Backend:** Python using FastAPI or Starlette, serving both the REST API and the static web assets.
- **Frontend stack:** Vanilla TypeScript or light reactive library with Tailwind CSS. Alternatively, HTMX with minimal client JavaScript for gesture recognition.
- **Email rendering:** Thread messages render inside isolated `iframe` elements using `srcdoc` or shadow DOM boundaries to prevent custom email styles from interfering with the application layout.
- **Touch interactions:** Touch event handlers bind swipe-left for archive and swipe-right for trash on thread list rows. A bottom action bar provides search, refresh, and tag-filtering buttons.
- **Attachment handling:** Standard HTML `<input type="file" multiple>` inputs trigger the mobile browser's native document, photo, and camera pickers.

### Strengths

- Fast deployment. Updating the server code immediately updates the mobile interface without re-compiling or re-installing an app.
- Platform independent. Runs identically on iOS Safari and Android Chrome.
- Zero app store or developer certificate requirements.
- Can be added to the mobile home screen as a standalone Progressive Web App with dedicated display mode.

### Limitations

- Touch gesture physics, such as rubber-banding and velocity tracking, can feel less immediate than native UIKit or Jetpack Compose components.
- Virtual keyboard appearance occasionally causes viewport resizing glitches in mobile browsers during reply composition.
- Background execution is limited, meaning background push notifications require external push relay infrastructure.

## Option 2: Thin native mobile client

A dedicated native mobile application developed in Swift for iOS or Kotlin for Android.

### Technical design

- **Platform:** Native iOS application using SwiftUI, or native Android application using Jetpack Compose.
- **Networking:** Standard system HTTP clients (`URLSession` or `OkHttp`) querying the host API through the device's active Tailscale connection.
- **List and navigation:** Built with native collection views (`List` or `LazyColumn`) that provide native 120Hz scrolling, pull-to-refresh, and fluid swipe action buttons for archiving and tagging.
- **Message display:** Native chrome headers for sender, date, and recipients, embedding a platform web view (`WKWebView` or `WebView`) strictly for the email body content.
- **Autocomplete:** Native search input wired with debounced API requests to `/api/addresses`, rendering suggested recipients in an overlay list.
- **File attachments:** Integrated directly with the system document picker and photo library.
- **Optional offline cache:** SQLite database storing thread summaries and message bodies. New actions enqueue to a local mutations table and replay against the host when connectivity restores.

### Strengths

- Best possible tactile responsiveness, including hardware haptic feedback on archive and tag triggers.
- Native keyboard avoidance animations when composing replies.
- No viewport zoom or scrolling conflicts between the application frame and the email body.
- Reliable offline caching structure if implemented.

### Limitations

- Higher build and maintenance overhead. Requires platform-specific build chains (Xcode on macOS for iOS, or Android Studio with Gradle for Android).
- iOS deployment requires either an active Apple Developer Program membership ($99 per year) or manual re-signing every 7 days.
- Code cannot be directly shared with the Python backend.

## Comparison summary

| Feature | Option 1: Web application | Option 2: Native application |
|---------|---------------------------|------------------------------|
| Development effort | Moderate (1 to 2 weeks) | High (4 to 8 weeks) |
| Device compatibility | Universal (iOS, Android, desktop browsers) | Platform-specific |
| Build dependencies | Python, Node.js or static assets | Xcode / Swift or Android Studio / Kotlin |
| Deployment workflow | Single command on Linux host | Device sideloading or TestFlight |
| Gesture fluidity | Good, browser-constrained | Native 120Hz with haptics |
| Keyboard handling | Sensitive to mobile browser viewport shifts | Native OS keyboard transitions |
| Attachment uploads | Native file input | Native system pickers |
| Contact autocomplete | Dynamic DOM dropdown | Native suggestion list |

## Implementation recommendation

The most practical approach is phased:

1. **Phase 1: Build the host API daemon in Lazarus.** Implement the shared endpoints in a new `lazarus/server` package. This delivers immediate utility and defines the data contracts.
2. **Phase 2: Deploy the Option 1 web interface.** Serve a mobile web interface from the daemon. Test the search, triage, reply, and attachment workflow on mobile over Tailscale.
3. **Phase 3: Evaluate native client need.** If the mobile web experience meets daily triage and reply needs, stop there. If gesture latency or browser keyboard quirks cause friction, the API daemon is already running and ready to back the Option 2 native client.

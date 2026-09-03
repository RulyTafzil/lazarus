# Mobile framework specification for Lazarus

This document defines the requirements, architecture, and implementation plans for extending Lazarus email access to mobile and remote devices. It evaluates how to evolve Lazarus into a client-server architecture inspired by MPD (Music Player Daemon) while strictly guarding against feature creep and preserving the zero-latency local desktop experience.

## Architectural principles and scope control

Before considering implementation details, the following guardrails govern all mobile and remote development:

1. **Local-first desktop primacy (anti-feature creep):**
   Over 90% of user interaction happens locally on the desktop client. Mobile and remote access are secondary extensions. The desktop client must never be forced to depend on an external network daemon. When running on a single workstation, Lazarus must remain completely self-contained in-process with zero network overhead.

2. **End-to-end encryption by default:**
   All communication between clients and the server daemon must be encrypted end-to-end at all times. The daemon will not expose unencrypted HTTP listeners over public or LAN networks.

3. **Additive headless core:**
   All shared logic lives in `lazarus.core` (file moves, sync engine, search, MIME building). Both the standalone desktop client and the server daemon import this core layer directly, ensuring zero duplicated domain logic without entangling the desktop GUI with remote networking.

## Evaluation: the MPD model for notmuch

### Has this already been done for notmuch?
In the 15-year history of the Notmuch ecosystem, a dedicated, general-purpose daemon analogous to MPD has never materialized. Instead, the community has relied on two workarounds:
- **SSH command wrapping:** Configuring local email clients (such as Emacs or Neomutt) to invoke `ssh host notmuch ...` for every search. This approach is brittle, introduces high latency per keypress, and fails to handle background IMAP sync, Maildir file moves, or attachments.
- **Muchsync:** Replicating the entire Notmuch database and Maildir directory tree to every laptop over SSH. While effective for laptops with large disks, it is completely impractical for mobile devices and requires syncing gigabytes of raw mail.

Building a headless daemon for Notmuch that manages synchronization, indexing, Maildir moves, and multi-client push notifications fills a genuine void in the ecosystem.

### Lessons to adopt from MPD
MPD provides several proven architectural patterns:
- **The `idle` event bus:** MPD avoids polling by letting clients issue an `idle` command. The server blocks until an internal subsystem changes (`database`, `playlist`, `player`), then wakes the client with the name of the modified subsystem. For Lazarus, a push event stream broadcasting changes (`mail`, `tags`, `moves`, `sync`) eliminates client-side polling timers.
- **Centralized side effects:** All background tasks (IMAP sync via `mbsync`, indexing via `notmuch new`, filter rules, and soft-delete/archive file moves) belong exclusively to the daemon. Clients become lightweight views that trigger actions without managing local subprocesses.
- **Unified multi-device state:** When a thread is archived or tagged on a mobile client, the desktop client receives the change notification and refreshes instantly.

### Where email departs from MPD
- **Rich document payloads vs control messages:** MPD exchanges tiny key-value strings because audio streams to hardware outputs. Email clients exchange nested JSON conversation trees, HTML bodies, and binary MIME attachments. A modern structured HTTP/REST interface with an event stream is far more appropriate than MPD's line-based Telnet protocol.
- **View state vs queue state:** MPD manages a shared playback queue. An email client's search query, selected tab, and scroll position are strictly local to that client.

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
   - Query the host's notmuch address index with substring matching.

5. **Local storage expectations**
   - Offline storage on the mobile device is optional. A live network client operating over Tailscale satisfies the core triage need.
   - The architecture must allow adding an offline cache layer later without redesigning the backend.

## Shared backend architecture

Both mobile options depend on a lightweight, headless API daemon running on the Linux host. The daemon exposes Lazarus and notmuch operations through HTTP endpoints.

### Network and security

- **End-to-end encryption by default:** Transport security is enforced via Tailscale WireGuard point-to-point encryption (ChaCha20-Poly1305). All traffic between client and server is encrypted at the network layer.
- **Interface binding:** The server binds strictly to the Tailscale interface (`100.x.y.z`) or loopback (`127.0.0.1`). It refuses to bind to 0.0.0.0 or unencrypted public interfaces.
- **TLS termination:** Optional direct HTTPS using Tailscale automated certificates (`tailscale cert`) or reverse proxy TLS.
- **Authentication:** Requests require an `Authorization: Bearer <token>` header matching a cryptographically secure token stored in the user's Lazarus configuration.
- **Process management:** Runs as a standard systemd user service (`systemd --user`) so it stays active when the Lazarus desktop GUI is closed.

### Reused Lazarus modules

The daemon avoids duplicating logic by importing pure Python modules from `lazarus.core`:

- `lazarus.core.actions`: File move planning, bulk worker, expunge, and restore.
- `lazarus.core.sync`: Parallel mbsync runner, indexing, and rule evaluation.
- `lazarus.notmuch`: CLI wrappers for `search_json`, `count_batch`, `tags`, and `tag`.
- `lazarus.mail_utils`: Message part decomposition, body extraction, and quoting logic.
- `lazarus.mime_builder`: RFC-compliant multipart MIME construction.
- `lazarus.rules`: Automated tag and folder filter rules.

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

## Current status and prudent next steps

1. **Phase 1 (Completed): Headless domain engine and server daemon.**
   Implemented `lazarus.core` (zero Qt dependencies, pure background worker, headless sync runner) and `lazarus.server` (REST API and static file router).
2. **Phase 2 (Completed): Option 1 mobile web application.**
   Deployed responsive, mobile-first web interface with pull-to-sync, one-tap triage, reply composition, dynamic signatures, and Tailscale WireGuard security.
3. **Phase 3 (Active hold): Real-world evaluation before further structural changes.**
   Given that 90% of usage remains on the local desktop client, hold on any further backend or native client rewrites. Use the mobile web interface daily to identify real-world bottlenecks before introducing new abstractions.
4. **Phase 4 (Optional future): MPD-style event streaming.**
   If polling overhead or desktop-mobile synchronization friction becomes noticeable in daily use, add a Server-Sent Events (SSE) stream (`/api/events`) to the daemon so clients receive push updates (`changed: mail`, `changed: tags`) modeled on MPD's `idle` architecture.

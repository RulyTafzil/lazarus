# NED (Notmuch Email Daemon) & Lazarus Client Architecture

This document defines the architecture, protocol, and roadmap for **NED (Notmuch Email Daemon)** and the evolution of Lazarus into a pure client. 

NED is a headless, always-on background email service inspired by the MPD (Music Player Daemon) architecture. It owns all interaction with local Maildirs, the Notmuch index, IMAP synchronization (`mbsync`), and outbound delivery (`msmtp`). Lazarus desktop, the mobile web interface, and future clients interact with NED as lightweight presentation layers.

---

## 1. Architectural principles and YAGNI guardrails

To prevent feature creep and architecture astronautics, the following principles govern NED:

1. **NED is explicitly for Notmuch:**
   NED is not a generic, pluggable storage platform. It does not abstract away Notmuch or invent a custom query DSL. Notmuch query syntax (`tag:inbox AND date:2w..today`) and native IDs (`thread:...`, message RFC Message-IDs) are first-class citizens.
2. **NED is the single concurrency boundary:**
   Notmuch supports concurrent readers, but only a single writer can access the Xapian index at any time. NED owns the single serialized write queue. Clients request mutations, and NED executes them sequentially so indexing, tagging, and Maildir moves never race or lock the database.
3. **SSE is for cache invalidation, not state replication:**
   Server-Sent Events (SSE) broadcast minimal invalidation signals (e.g. `thread.updated`, `mail.synced`). Clients do not reconstruct state through complex event replay or delta-patching. When an invalidation event touches the active view, the client re-queries NED.
4. **Single-user simplicity:**
   NED is a personal email daemon. Authentication requires a single cryptographically secure bearer token over Tailscale WireGuard. Local IPC uses Linux filesystem permissions on a Unix domain socket. Multi-tier ACLs, permission scopes, and complex user management are rejected.
5. **No generic job queue:**
   Email mutations (tagging, moving, search) take milliseconds and execute synchronously. Only IMAP sync takes seconds; sync state is tracked with a single busy flag and an SSE completion event. Heavy distributed job queues (Celery/UUID polling) are rejected.
6. **Unified daemon, bundled web assets:**
   NED directly serves the static mobile PWA assets (`index.html`, `app.js`, `app.css`) on its HTTP listener. Users do not need to configure or maintain an external reverse proxy or separate web server.

---

## 2. System architecture

```
                     ┌────────────────────────────────────────┐
                     │          NED (Always-on Daemon)        │
                     │  - Serialized write queue (Lock)       │
                     │  - Holds Maildir (~/Mail) & Notmuch DB │
                     │  - Runs mbsync, notmuch new, rules     │
                     │  - SSE Invalidation Broadcaster        │
                     │  - Serves static mobile PWA assets     │
                     └───────────────────┬────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 │                                               │
     Local IPC (Unix Socket)                         Tailscale Network (HTTPS/WireGuard)
  /run/user/1000/ned/ned.sock                        100.x.y.z:8080 (Bearer Token)
                 │                                               │
                 ▼                                               ▼
         ┌───────────────┐                               ┌───────────────┐
         │  ned_client   │                               │  Mobile Web   │
         └───────┬───────┘                               │    (or App)   │
                 ▼                                       └───────────────┘
     ┌───────────────────────┐
     │    Lazarus Desktop    │
     │   (Snappy PyQt6 GUI)  │
     │  - Pure NED client    │
     │  - Listens to SSE     │
     └───────────────────────┘
```

### Transports
- **Local IPC (Unix Domain Socket):**
  Located at `/run/user/$UID/ned/ned.sock`. Communicates via standard HTTP/1.1 over the Unix socket (`http+unix://...`). Delivers 10-20 microsecond latency, zero TCP port collisions, and standard OS permission security.
- **Remote Network (Tailscale):**
  Binds strictly to the Tailscale WireGuard interface (`100.x.y.z:8080`) or localhost. Refuses to bind to `0.0.0.0` or unencrypted public interfaces. All traffic is end-to-end encrypted at the network layer via ChaCha20-Poly1305.

---

## 3. The API v1 specification

All endpoints are versioned under `/api/v1/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/threads` | Search threads. Query params: `q` (notmuch query), `limit`, `offset`. |
| `GET` | `/api/v1/threads/{id}` | Fetch full thread tree with messages and metadata. |
| `GET` | `/api/v1/messages/{id}/part/{part_id}` | Download decoded message body part or binary attachment. |
| `POST` | `/api/v1/tags` | Modify tags. Body: `{"query": "...", "add": [...], "remove": [...]}`. |
| `POST` | `/api/v1/threads/{id}/archive` | Archive thread (`-inbox -unread` + move to `Archive/cur/`). |
| `POST` | `/api/v1/threads/{id}/trash` | Trash thread (`+trash -inbox -unread` + move to `Trash/cur/`). |
| `POST` | `/api/v1/threads/{id}/unarchive` | Restore archived thread to `inbox`. |
| `POST` | `/api/v1/threads/{id}/untrash` | Restore trashed thread from `Trash/cur/` to `INBOX/cur/`. |
| `GET` | `/api/v1/tags` | List all known Notmuch tags with thread counts. |
| `GET` | `/api/v1/contacts` | Address autocomplete matching prefix `q` via `notmuch address`. |
| `GET` | `/api/v1/reply-seed` | Generate reply recipient headers, quoted body, and signature. |
| `POST` | `/api/v1/send` | Send outbound message via `msmtp`. |
| `POST` | `/api/v1/sync` | Trigger IMAP sync + `notmuch new` + filter rules. |
| `GET` | `/api/v1/events` | Server-Sent Events (SSE) stream for cache invalidation. |
| `GET` | `/` | Serves the mobile PWA web application. |

### Invalidation event schema
The SSE stream (`GET /api/v1/events`) emits simple JSON events:
```text
event: invalidate
data: {"scope": "threads", "reason": "sync"}

event: invalidate
data: {"scope": "thread", "id": "0000000000001234", "reason": "tag"}
```

---

## 4. Implementation roadmap

### Phase 1: The NED daemon (Active)
- Implement `lazarus.ned` daemon with dual listeners (Unix domain socket + optional Tailscale TCP).
- Implement a serialized mutation lock (`threading.Lock` / `asyncio.Lock`) protecting Maildir and Notmuch write operations.
- Implement `/api/v1/` route registry absorbing `core.actions`, `core.sync`, and query handlers.
- Implement minimal SSE invalidation broadcaster (`/api/v1/events`).
- Serve bundled static mobile web assets.
- Provide `systemd --user` unit file (`ned.service`).

### Phase 2: Python client library (`ned_client.py`)
- Create lightweight, zero-dependency Python client supporting both Unix domain socket and HTTP transports.
- Wrap all `/api/v1/` methods with clean typed signatures.
- Provide automated unit tests verifying client against running NED instance.

### Phase 3: Migrate Lazarus desktop to pure client
- Refactor Lazarus desktop panels (`search.py`, `thread.py`, `actions.py`, `controller.py`) to call `ned_client.py`.
- Connect desktop panel auto-refresh to NED's SSE invalidation stream.
- Remove direct `subprocess.Popen(['notmuch', ...])` and local `_BulkMoveWorker` from desktop.

### Phase 4: Retire `lazarus-server`
- Deprecate `lazarus-server` CLI in favor of `ned`.
- Update user documentation and `agent.md`.

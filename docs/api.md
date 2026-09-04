# NED HTTP API

NED (the **N**otmuch **E**mail **D**aemon) exposes a versioned HTTP API under
`/api/v1/` over two transports. The mobile PWA served at `/` is a full client,
and the desktop GUI is another — anything a client needs (search, thread
assembly, parts, replies, send, sync, rules) is here.

A machine-readable [OpenAPI 3.0](https://swagger.io/specification/) description
of the running daemon is served live at:

```
GET /api/v1/openapi.json
```

## Transports & auth

| Transport | Where | Auth |
|---|---|---|
| Unix domain socket | `/run/user/$UID/ned/ned.sock` (default) | none — OS file permissions |
| TCP | Tailscale IP or `settings.web_host` on `settings.web_port` (default `8080`) | optional Bearer token (`settings.web_token`) |

Token options: `Authorization: Bearer <token>` header, or `?token=<token>`
query parameter (needed for the SSE `EventSource`, which cannot set headers).

When bound over WireGuard/Tailscale, transport security comes from the tailnet;
when bound to the loopback device, it is local-only. Binding a plain-LAN or
public interface with an **empty** `web_token` leaves the API unauthenticated —
set a token or keep it behind Tailscale.

## Conventions

- **Reads** return raw JSON: thread/message payloads are passthrough
  `notmuch search/show --format=json` dicts (see `notmuch(1)` for the message
  schema). Lists are top-level JSON arrays.
- **Mutations** return an envelope: `{"status": "ok", "ok": true, ...}`.
- **Errors** return a non-2xx status with `{"error": "<message>"}`.
- Path parameters are URL-encoded message/thread ids (e.g. `%40` for `@`).
- Every prefix has a legacy alias under `/api/` (e.g. `/api/threads`), which is
  internally normalized to `/api/v1/`.

## Endpoints

### Search & read

#### `GET /api/v1/threads` — search threads
Query params: `q` (notmuch query; default `tag:inbox`), `limit` (default 50), `offset` (default 0).

```text
GET /api/v1/threads?q=from:github&limit=10
→ 200 [ {"thread": "0000000000000001", "timestamp": 1700000000,
        "authors": "GitHub", "subject": "…", "total": 3, "tags": ["inbox"]}, … ]
```

#### `GET /api/v1/threads/{id}` — one thread tree
```text
GET /api/v1/threads/0000000000000001
→ 200 { "thread_id": "…", "messages": [ … notmuch show JSON … ] }
```

#### `GET /api/v1/messages` — search messages
Query params: `q` (required), `limit` (default 1000), `offset`.

```text
GET /api/v1/messages?q=tag:unread
→ 200 ["id:0000000000000001@example.com", …]
```

#### `GET /api/v1/messages/{id}` — one raw message dict
```text
GET /api/v1/messages/id%3Aabc123%40example.com
→ 200 { "id": "…", "headers": {…}, "body": [ …notmuch part dicts… ], "tags": […], … }
```

#### `GET /api/v1/messages/{id}/parts/{part_id}` — decoded part / attachment
Returns the raw bytes with the part's `Content-Type`; attachments also carry
`Content-Disposition: attachment; filename="…"`.

```text
curl --unix-socket /run/user/$UID/ned/ned.sock \
  http://localhost/api/v1/messages/id%3Aabc%40example.com/parts/3 -o invoice.pdf
```

#### `GET /api/v1/count` — counts
`get`: `?q=<query>&output=threads|messages` → JSON number.
`post`: `{"queries": [...]}` → JSON array of counts (batch).

#### `GET /api/v1/tags` — tags with counts
```text
GET /api/v1/tags
→ 200 [ {"name": "inbox", "count": 12}, … ]
```

#### `GET /api/v1/contacts` — address autocomplete
`?q=<prefix>` → list of `{"name":…, "address":…}` dicts.

### Compose support

#### `GET /api/v1/accounts` — sender accounts + identity
```text
GET /api/v1/accounts
→ 200 { "accounts": ["gmail", "contact"],
        "email": { "gmail": "Ruly Tafzil <RulyTafzil@gmail.com>", … },
        "gnupg_keyid": { "gmail": null, … } }
```

#### `GET /api/v1/signatures` — per-account signatures
```text
GET /api/v1/signatures
→ 200 { "use_signature": true,
        "signatures": { "contact": "-- \nRuly\n" },
        "signatures_html": { "contact": "<p>Ruly</p>" } }
```

#### `GET /api/v1/messages/{id}/reply-seed` — reply scaffold
`?to_all=<bool>` → `{ "to": "…", "cc": "…", "subject": "RE: …", "body": "\n\n> quoted…" }`
(recipients already filtered to exclude the sender's own addresses; body
includes the quoted original and, when `use_signature`, the signature).

### Mutations

The daemon serializes all mutations under a single write lock. Mutations divide into two distinct operations: index tag mutations and Maildir filesystem moves.

#### Index tag mutations

Index tag mutations modify Notmuch Xapian tags without moving files on disk. Use these for marking read or unread, flagging, custom labels, and inbox zero archiving.

##### `POST /api/v1/tags`

Modify tags across queries, threads, or messages.

```json
{
  "queries": ["tag:marked AND tag:inbox", "tag:unread"],
  "threads": ["0000000000001234"],
  "messages": ["msgid@example.com"],
  "add": ["reviewed"],
  "remove": ["marked"]
}
```

Accepts optional `queries`, `threads`, and `messages` arrays. Provide at least one target and at least one tag in `add` or `remove`. Every item in `queries` is an unparsed Notmuch query string. Threads and messages receive their `thread:` and `id:` prefixes without string guessing. Legacy parameters `ids` and `query` remain supported.

##### `POST /api/v1/threads/{id}/tags`

Modify tags on a single thread.

```json
{ "add": ["reviewed"], "remove": ["unread"] }
```

##### `POST /api/v1/messages/{id}/tags`

Modify tags on a single message.

```json
{ "add": ["replied"], "remove": [] }
```

##### `POST /api/v1/threads/{id}/star`

Set or clear the flagged tag on a thread.

```json
{ "flag": true }
```

#### Maildir filesystem operations

Maildir moves relocate email files on disk and synchronize Notmuch tags. Each batch operation accepts an optional `unmark` boolean to clear the `marked` tag.

##### `POST /api/v1/trash`

Move matching files to account Trash directories, tag `+trash -inbox -unread`, and optionally remove `marked`.

```json
{
  "queries": ["tag:marked AND tag:inbox"],
  "threads": ["0000000000001234"],
  "messages": ["msgid@example.com"],
  "unmark": true
}
```

##### `POST /api/v1/threads/{id}/trash`

Move all files in a thread to its account Trash directory and tag `+trash -inbox -unread`.

##### `POST /api/v1/messages/{id}/trash`

Move a single message file to its account Trash directory and tag `+trash -inbox -unread`. Accepts optional query parameter `thread_id` to emit thread-specific cache invalidation.

##### `POST /api/v1/restore`

Restore matching files from Trash back to account INBOX directories, tag `-trash +inbox`, and optionally remove `marked`.

```json
{
  "queries": ["tag:marked AND tag:trash"],
  "threads": ["0000000000001234"],
  "messages": ["msgid@example.com"],
  "unmark": true
}
```

##### `POST /api/v1/threads/{id}/restore`

Restore thread files from Trash back to INBOX and tag `-trash +inbox`.

##### `POST /api/v1/messages/{id}/restore`

Restore a single message file from Trash back to INBOX and tag `-trash +inbox`. Accepts optional query parameter `thread_id` to emit thread-specific cache invalidation.

##### `POST /api/v1/move-archive`

Move matching files to the local Archive Maildir, tag `-inbox -unread`, and optionally remove `marked`.

```json
{
  "queries": ["tag:marked AND tag:inbox"],
  "threads": ["0000000000001234"],
  "messages": ["msgid@example.com"],
  "unmark": true
}
```

##### `POST /api/v1/threads/{id}/move-archive`

Move all files in a thread to the local Archive Maildir and tag `-inbox -unread`.

##### `POST /api/v1/messages/{id}/move-archive`

Move a single message file to the local Archive Maildir and tag `-inbox -unread`. Accepts optional query parameter `thread_id` to emit thread-specific cache invalidation.

##### Legacy move aliases

The following legacy endpoints remain available for backwards compatibility:
- `POST /api/v1/threads/trash` forwards to `POST /api/v1/trash`
- `POST /api/v1/threads/{id}/untrash` and `POST /api/v1/threads/untrash` forward to `restore`
- `POST /api/v1/threads/{id}/archive` and `POST /api/v1/threads/archive` forward to `move-archive`
- `POST /api/v1/threads/{id}/unarchive` and `POST /api/v1/threads/unarchive` restore to inbox by adding tag `inbox`

##### `POST /api/v1/expunge`

Apply the Maildir T flag to every file matching `tag:trash`. Irreversible.

### Send

#### `POST /api/v1/send` — outbound mail, two modes
**Field mode** (simple text, or multipart/form-data with attachments):
```json
{ "account": "contact", "to": ["a@b.c"], "cc": [], "bcc": [],
  "subject": "hi", "body_text": "hello", "in_reply_to": "", "references": "" }
```
**Raw MIME mode** (client-built message: rich HTML, inline images, PGP — the
desktop uses this):
```json
{ "account": "contact", "message_b64": "RnJvbTog…(base64 RFC822 message)" }
```
The daemon pipes the message through `settings.send_mail_command` (msmtp),
saves a sent copy to `settings.sent_dir`, indexes it, and broadcasts.

### Maintenance

| Endpoint | Effect |
|---|---|
| `POST /api/v1/sync` | parallel `mbsync -V <acct>` per account + `notmuch new` + filter rules; returns `{status, message}` summary |
| `POST /api/v1/rules` | apply `settings.filter_rules`; returns `{status, matched}` |
| `POST /api/v1/index` | `notmuch new --no-hooks` (sent-mail append) |
| `GET /api/v1/ping` / `/api/v1/health` | liveness; `{status: ok, service: ned}` |

### Server-Sent Events

`GET /api/v1/events` streams cache invalidations — clients re-query on
invalidation rather than patching state:

```text
event: invalidate
data: {"scope": "threads", "reason": "sync"}

event: invalidate
data: {"scope": "thread", "id": "0000000000001234", "reason": "tag"}
```

## Building a client

Worked example (Unix socket, Python stdlib):

```python
import http.client, json, socket

class NedHTTP(http.client.HTTPConnection):
    def __init__(self, sock): super().__init__("localhost"); self.sock_path = sock
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.sock_path)

c = NedHTTP("/run/user/$UID/ned/ned.sock")
c.request("GET", "/api/v1/threads?q=tag:inbox&limit=5")
resp = c.getresponse()
print(json.loads(resp.read()))     # [thread dicts]
c.request("POST", "/api/v1/tags", json.dumps({"queries": ["thread:…"], "add": ["seen"]}),
          {"Content-Type": "application/json"})
print(resp.status)                 # 200
```

For a complete, typed reference client see `ned/client.py` (the
`ned-client` CLI wraps the same surface), and `GET /api/v1/openapi.json`
for the machine-readable spec.
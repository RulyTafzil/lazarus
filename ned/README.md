# ned

Notmuch Email Daemon. Headless notmuch index and Maildir mutation service with Server-Sent Events, HTTP REST API, and bundled mobile web client.

## Overview

NED manages notmuch indexing, Maildir synchronization, mutation locks, and cache invalidation. Clients connect via a Unix domain socket or over Tailscale TCP with Bearer authentication.

## Installation

```bash
pipx install ./ned
```

## Running the daemon

Start NED on its default Unix domain socket:

```bash
ned
```

Enable the network listener:

```bash
ned --host 127.0.0.1 --port 8080
```

Generate an initial configuration file:

```bash
ned --init-config
```

## Configuration

NED reads its configuration exclusively from `~/.config/ned/config.py`.

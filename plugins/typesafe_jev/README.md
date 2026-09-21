# TypeSafe System One (Jev) Post-Sync Plugin for NED

An optional asynchronous post-sync plugin for NED that evaluates incoming email
metadata (strictly `from` and `subject`) using TypeSafe System One across two facets:
Document Type and Domain.

## Installation

1. Copy this plugin directory into your NED config plugins folder:
   ```bash
   mkdir -p ~/.config/ned/plugins
   cp -r plugins/typesafe_jev ~/.config/ned/plugins/
   ```

2. Open `~/.config/ned/plugins/typesafe_jev/taxonomy.py` and configure your API key:
   ```python
   API_KEY = "ts_..."
   ```
   You can also customize the document type and domain rubrics in that file.

3. Enable the plugin in `~/.config/ned/config.py`:
   ```python
   from typesafe_jev import TypeSafeJevPlugin

   ned.settings.post_sync_plugins = [
       TypeSafeJevPlugin(),
   ]
   ```
   *(Or specify by import string: `'typesafe_jev.TypeSafeJevPlugin'`)*

4. Restart NED:
   ```bash
   # If using systemd:
   systemctl --user restart ned
   # If using OpenRC (Alpine):
   rc-service ned restart
   ```

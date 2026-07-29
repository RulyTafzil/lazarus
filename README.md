# Lazarus

Lazarus is a fork of [Dodo](https://github.com/akissinger/dodo) by Aleks Kissinger,
a graphical email client written in Python/PyQt6, based on the command line
email swiss-army-knife [notmuch](https://notmuchmail.org/).

![Dodo](/images/dodo-screen-plus-icon.png?raw=true)

It's main goals are to:

* offer efficient, keyboard-oriented mail reading, sorting, and composing
* give a mostly text-based email experience by default, but with HTML support a few keystrokes away
* offload as much work as possible on existing, excellent command-line tools (UNIX philosphy-style)
* be simple enough to customise and hack on yourself

This README has instructions on installation, usage, and basic configuration. For API documentation (which is also useful for configuration), check out the [Read the Docs](https://dodomail.readthedocs.io/en/latest/) page.

As an email client, Lazarus is pretty much feature-complete, but **not yet extensively tested**. Since it's based on notmuch, all of its features are non-destructive, so you shouldn't ever lose any email due to bugs. That being said, you might see some strange behaviour, so use at your own risk.

A lot of Lazarus's design is inspired by two existing notmuch-based clients: [alot](https://github.com/pazz/alot) and [astroid](https://github.com/astroidmail/astroid).


## Prerequisites

If you have already used `notmuch` for email, there's not much to do here :). If not, you'll need to set up some other programs first:

* something to check mail and sync with a local Maildir ([offlineimap](http://www.offlineimap.org/) is the default, but others like [mbsync](https://isync.sourceforge.io/) should work fine)
* a sendmail-compatible SMTP client to send mail ([msmtp](https://marlam.de/msmtp/) is the default)
* [notmuch](https://notmuchmail.org/) for email searching and tagging
* [w3m](http://w3m.sourceforge.net/) for translating HTML messages into plaintext
* [python-gnupg](https://pypi.org/project/python-gnupg/) for pgp/mime support (optional)

All of this is pretty standard stuff, and should be installable via your package manager on Linux/Mac/etc. If you don't know how to set these things up already, see the respective websites or the "Setting up the prerequisites" section below for a quick reference.


## Install and run

Lazarus requires Python 3.7+ and [PyQt6](https://riverbankcomputing.com/software/pyqt/intro) 6.2 or above. You can install the latest git version of Lazarus and its dependencies using [pip](https://pypi.org/project/pip/):

    git clone https://github.com/akissinger/dodo.git
    cd lazarus
    pip install .
    
Then, run Lazarus with:

    lazarus

If you don't have it already, you may need to add `~/.local/bin` to your `PATH`.

## Basic use

Before you fire up Lazarus for the first time, make sure you at least configure `email_address` and `sent_dir` in `config.py` (see next section).

Most functionality in Lazarus comes from keyboard shortcuts. Press `?` to get a full list of the key mappings at any time.

Lazarus has 4 different kinds of view: search views, thread views, compose views, and the tag view. It opens initially with a search view with the query `tag:inbox`. Pressing enter or double-clicking a thread with open that thread in the thread view. Pressing `c` at any time or `r` while looking at a message in the thread view will open the compose view. Pressing `T` will open a list of all the known tags in a new tab.

In the compose view, press `<enter>` to edit the message on your chosen editor. Once you save and exit, the message will be updated. Press `a` to add attachments (or use the special `A:` header). Press `S` to send.


## Configuration

Lazarus is configured via `~/.config/lazarus/config.py`. This is just a Python file that gets `eval`-ed right before the main window is shown.

Settings and their default values are defined in [settings.py](https://github.com/akissinger/dodo/blob/master/dodo/settings.py). A complete list, with documentation, can be found [here](https://dodomail.readthedocs.io/en/latest/api.html#module-dodo.settings).

Most settings have reasonable defaults (assuming your are using offlineimap/msmtp). The only two things that must be set for Lazarus to work properly are your email address and the location of your sent mail folder. Some things you probably also want to set up are the text editor (for composing messages) and the file browser (for viewing attachments).

Here is an example `config.py`, with some settings similar to the ones I use:

```python
import lazarus

# required
lazarus.settings.email_address = 'First Last <me@domain.com>'
lazarus.settings.sent_dir = '/home/user/mail/Work/Sent'

# optional
lazarus.settings.theme = lazarus.themes.nord
lazarus.settings.editor_command = "kitty nvim '{file}'"
lazarus.settings.file_browser_command = "fman '{dir}' /home/user/Documents"
```

A theme is just a Python dictionary mapping some fixed color names to HTML color codes. Currently, the themes implemented in [themes.py](https://github.com/akissinger/dodo/blob/master/dodo/themes.py) are `catppuccin_macchiato`, `nord`, `solarized_light` and `solarized_dark`. If you want more, feel free to roll your own, or (better) send me a pull request!

All of the settings of the form `..._command` are given as shell command. The `editor_command` setting takes a placeholder `{file}` for the file to edit and `file_browser_command` takes the placeholder `{dir}` for the directory to browse.

The settings above replace the default text editor (`xterm -e vim`) with [neovim](https://neovim.io/) run inside a new [kitty](https://sw.kovidgoyal.net/kitty/) terminal. I am also using Michael Herrmann's excellent dual-pane file manager [fman](https://fman.io/) instead of the default (`nautilus`). With these settings, showing attachments will open `fman` with a fixed directory in the right pane (`/home/user/Documents`) and a directory containing the attachments on the left. A similar effect can be obtained with [ranger](https://github.com/ranger/ranger) using the `multipane` view mode.

If you are using a file browser that supports it, you can also set a custom `file_picker_command` for choosing attachments. This setting is `None` by default, which tells Lazarus to use the built-in file picker. This accepts a `{tempfile}` placeholder, where the names of the chosen files should be written after running the command. Here's an example using `ranger --choosefiles`:

    lazarus.settings.file_picker_command = "kitty ranger --choosefiles='{tempfile}'"

While Javascript is disabled in the HTML email viewer, you may want to set up a custom HTML sanitizer function as follows:

    lazarus.util.html2html = lazarus.util.clean_html2html

The above function passes the HTML through the `Cleaner` object of the [bleach](https://github.com/mozilla/bleach) library. Note this still allows some dodgy stuff, such as calling home via embedded `img` tags, so remote requests from HTML messages are disabled by default via the setting `html_block_remote_requests`. Javascript is also disabled.

### Key mapping

Key mappings can be customised by changing the dictionaries defined in [keymap.py](https://github.com/akissinger/dodo/blob/master/dodo/keymap.py). These map a key to a pair consisting of a description string and a Python function. For the `global_keymap`, this function takes the `Dodo` object defined in [app.py](https://github.com/akissinger/dodo/blob/master/dodo/app.py) as its argument. The other maps take the relevant "local" widget (SearchView, ThreadView, ComposeView, or CommandBar).

To bind a single key, you can write something like this in `config.py`:

```python
lazarus.keymap.search_keymap['t'] = (
  'toggle todo',
  lambda p: p.toggle_thread_tag('todo'))
```
or you can replace the keymap completely from `config.py`, e.g.:
```python
lazarus.keymap.search_keymap = {
  'C-n': ('next thread', lambda p: p.next_thread()),
  'C-p': ('previous thread', lambda p: p.previous_thread()),
  # ...
}
```

The keymaps used by Lazarus are `global_keymap`, `search_keymap`, `thread_keymap`, and `command_bar_keymap`. All the keymaps except `command_bar_keymap` also support keychords, which are represented as space-separated sequences of keypresses, e.g.
```python
lazarus.keymap.global_keymap['C-x C-c'] = (
  'exit emacs ... erm, I mean Lazarus',
  lambda a: a.quit())
```

You can unmap a single key by deleting it from the dictionary:
```python
del lazarus.keymap.global_keymap['Q']
```


### Multiple accounts

If you are using something like [msmtp](https://marlam.de/msmtp/) to send emails, it is possible to send mail from multiple accounts. To set this up, simply set a list of account names your SMTP client recognises in `config.py`. You can also provide per-account email addresses and sent directories by passing dictionaries to `email_address` and `sent_dir` settings, respectively. The `gnupg_keyid` setting can also be set to a dictionary mapping account names to GPG key IDs. If an account name is missing, signing is automatically disabled when switching to the account in question.

```python
import lazarus

lazarus.settings.smtp_accounts = ['work', 'fun']

lazarus.settings.email_address = {'work': 'First Last <me@super-serious-company.com>',
                               'fun': 'First Last <me@super-silly-domain.ninja>'}
lazarus.settings.sent_dir =      {'work': '/home/user/mail/Work/Sent',
                               'fun': '/home/user/mail/Fun/Sent'}
```

By default, you can use the `[` and `]` keys to cycle through different accounts in the Compose panel. The first account in the list is selected by default.

For multiple incoming mail accounts, just sync all accounts into subdirectories of a single directory and point `notmuch` to the main directory.


### Signatures

Signatures are per-account and are loaded from files, not from `config.py` — that keeps a signature (personal data) separate from configuration (which you might keep in version control), and lets you manage it with other tools (a symlink to a company template, a script, etc.) independently of Lazarus.

For each account name in `lazarus.settings.smtp_accounts`, Lazarus looks for:

```
$XDG_CONFIG_HOME/lazarus/<account>/signature       # plain text
$XDG_CONFIG_HOME/lazarus/<account>/signature.html  # HTML
```

(`$XDG_CONFIG_HOME` defaults to `~/.config`.) So, continuing the two-account example above, you'd create:

```
~/.config/lazarus/work/signature
~/.config/lazarus/fun/signature
```

Either file is optional, and you don't need both. If only `signature.html` exists, its plain-text rendering (via `lazarus.util.html2text`) is used as a fallback in the meantime — Lazarus's compose editor is currently plain text, so `signature.html` is read and stored but not yet inserted directly; it'll be used as-is once a rich-text compose mode lands.

The signature is inserted automatically when you open a new message, reply, or forward, right after the area where you type and before any quoted or forwarded text below it. Set `lazarus.settings.use_signature = False` to disable this entirely.


### Mail filters

Filter rules let Lazarus automatically tag and file incoming mail based on a
notmuch query, similar to filters/rules in other mail clients. They run
automatically after every sync, and can also be re-run on demand with the
``C-r`` key (useful for testing a new rule against mail you already have,
without waiting for the next sync).

Configure a list of ``lazarus.rules.Rule`` objects as
``lazarus.settings.filter_rules`` in ``config.py``:

```python
import lazarus
from lazarus.rules import Rule

lazarus.settings.filter_rules = [
    # GitHub notifications: tag, remove from inbox, and file to Archive
    Rule(
        name='github notifications',
        query='from:notifications@github.com',
        tag_add=['github'],
        tag_remove=['inbox', 'unread'],
        move_to='~/Mail/Archive',
    ),

    # Flag anything from your manager (tag only, no move)
    Rule(query='from:boss@work.com', tag_add=['flagged', 'work']),
]
```

Each ``Rule`` has:

- ``query`` — a notmuch query (same syntax as the search bar) selecting
  which messages the rule applies to
- ``tag_add`` / ``tag_remove`` — lists of tags to add/remove from matching
  messages (either can be omitted)
- ``move_to`` — optional path to a Maildir folder.  Matching messages are
  moved into ``<move_to>/cur/`` after any tags are applied (e.g.
  ``'~/Mail/Archive'`` or ``'~/Mail/Work/Projects'``).  File moves use
  the same background worker and stale-path resolution as the ``A``
  archive-to-local hotkey.
- ``name`` — optional, just used to identify the rule in log messages

A rule needs at least one of ``tag_add``, ``tag_remove``, or ``move_to``
to do anything.

#### Query discipline

Rules are applied as ``(filter_scope_query) and (rule.query)``, where
``filter_scope_query`` defaults to ``'tag:inbox and tag:unread'`` —
freshly-arrived, not-yet-triaged mail.  This scope is what keeps a rule
from re-tagging your entire archive every time it runs.

**Construct your rules so a message no longer qualifies after the rule
has acted on it.**  For example, if your scope is ``tag:inbox and
tag:unread`` and a rule moves mail to Archive, include
``tag_remove=['inbox', 'unread']`` so the message falls out of the scope
on the next sync.  If you omit that, the message still matches the scope
and the rule will try to file it again (and again on every sync).

As a safety net, ``move_files`` skips files whose source path is already
under the target directory, so you won't get silent duplicates even if
the tags don't update.  But the rule will still spin up subprocesses
unnecessarily, so matching your tags to your scope is the right fix.

Adjust the scope in ``config.py`` if your workflow needs it:

```python
lazarus.settings.filter_scope_query = 'tag:inbox and tag:unread and date:7d..'
```

If you want folder-based filing that runs whether or not Lazarus itself is
open (e.g. mapping a Gmail label to a local folder), that belongs at the
notmuch-hook level (``~/.config/notmuch/default/hooks/post-new``).  Rules
here are for things you want tagged and organised whenever Lazarus happens
to sync.


### Custom commands with the command bar

By default, the command bar can be opened in two modes, `'search'` and `'tag'`, for searching and tagging messages, respectively. You can create more modes on-the-fly from `config.py` by passing a new name and a Python callback function to [CommandBar.open](https://dodomail.readthedocs.io/en/latest/api.html#dodo.commandbar.CommandBar.open). Here's an example which creates a new mode called `'notmuch'` for running arbitrary notmuch commands:

```python
import lazarus
import subprocess

def run_notmuch(app):
    def callback(cmd):
        subprocess.run('notmuch ' + cmd, shell=True)
        app.refresh_panels()
    app.command_bar.open('notmuch', callback)

lazarus.keymap.global_keymap['N'] = ('run notmuch from command bar', run_notmuch)
```

### Custom layouts

You can customise the layout of the thread view by replacing the method `ThreadPanel.layout_panel` with your own version in `config.py`. This method is responsible for displaying three widgets: `self.thread_list`, `self.message_info`, and `self.message_view`. Normally, it draws the first two side-by-side, then places this above the third with an adjustable splitter.

Here's an example, making thread list twice as wide and putting the message view on top instead:

```python
def my_layout(self):
    splitter = QSplitter(Qt.Orientation.Vertical)
    info_area = QWidget()
    info_area.setLayout(QHBoxLayout())
    self.thread_list.setFixedWidth(500)
    info_area.layout().addWidget(self.thread_list)
    info_area.layout().addWidget(self.message_info)
    splitter.addWidget(self.message_view)
    splitter.addWidget(info_area)
    self.layout().addWidget(splitter)

    # save splitter position
    window_settings = QSettings("lazarus", "lazarus")
    state = window_settings.value("thread_splitter_state")
    splitter.splitterMoved.connect(
            lambda x: window_settings.setValue("thread_splitter_state", splitter.saveState()))
    if state: splitter.restoreState(state)

lazarus.thread.ThreadPanel.layout_panel = my_layout
```

Note that everything in `PyQt6.QtCore` and `PyQt6.QtWidgets` is already imported before `config.py` is exec'ed.


### Snooze

Snoozing lets you temporarily hide messages to help clear your inbox (and your mind) for a few days at a time. After the snooze is up, they pop back into the inbox as unread messages again. Using `notmuch` hooks and Lazarus, it is easy to set up some basic snooze functionality. Here's how I do it.

The basic idea is to tag messages with `zzz-` plus the date you want to see them again, then archive them. To make sure they pop back into the inbox on the correct date, add the following `~/MAILDIR/.notmuch/hooks/pre-new`:

```bash
#!/bin/bash

notmuch tag -zzz-`date -I` +inbox +unread -- tag:zzz-`date -I`
```

This will automatically un-snooze messages tagged `zzz-CURRENT-DATE` whenever you refresh your email. Now, all you need to do is set up some keyboard shortcuts for snoozing messages. You can do this by adding the following to your `config.py`:

```python
def snooze(days, mode='tag'):
    import datetime
    d = datetime.date.today() + datetime.timedelta(days=days)
    def f(search):
        search.tag_thread(f'-inbox -unread +zzz-{d}', mode)
    return f

lazarus.keymap.search_keymap['z z'] = ("snooze for 1 day", snooze(days=1))
lazarus.keymap.search_keymap['z w'] = ("snooze for 1 week", snooze(days=7))
lazarus.keymap.search_keymap['z Z'] = ("snooze marked for 1 day", snooze(days=1, mode='tag marked'))
lazarus.keymap.search_keymap['z W'] = ("snooze marked for 1 week", snooze(days=7, mode='tag marked'))
```

This allows snoozing single messages or bulk snoozing all marked messages (by default, you can (un)mark messages with `<space>`).

Note this doesn't return messages to the top, since it doesn't change the date they were received. Hence, it will work best if you keep your inbox relatively empty.



## Setting up the prerequisites

Since there's a lot of little bits to configure, I've also included some minimal configurations for offlineimap, msmtp, and notmuch, just to have it all in one place.

Note the offlineimap and msmtp configurations below simply read the password from a plaintext file. More secure options are available, which are explained in the respective docs.

### Incoming mail

Assuming your system configuration directory is `~/.config/`, the configuration file for offlineimap is located in `~/.config/offlineimap/config`. Here is a template for syncing one IMAP account named "Work":

    [general]
    accounts = Work

    [Account Work]
    localrepository = WorkLocal
    remoterepository = WorkRemote

    [Repository WorkLocal]
    type = Maildir
    localfolders = ~/mail/Work

    [Repository WorkRemote]
    type = IMAP
    remotehost = (IMAP SERVER)
    remoteuser = (USER NAME)
    remotepassfile = (PASSWORD FILE)
    sslcacertfile = OS-DEFAULT

If you want to set up multiple IMAP accounts, just put them all in the `~/mail` folder and set `~/mail` as your database path for notmuch.

### Outgoing mail

Here is a sample `~/.config/msmtp/config`, setting up a single SMTP server (also named "Work") with TLS:

    defaults
    auth           on
    tls            on
    tls_trust_file /etc/ssl/certs/ca-certificates.crt
    logfile        ~/.msmtp.log
    account        Work
    host           (SMTP SERVER)
    port           587
    from           (EMAIL ADRESS)
    user           (USER NAME)
    passwordeval   cat (PASSWORD FILE)
    account        default : Work

You may need to change the 4th line if your system stores CA certificates in a different location.

### Mail indexing

Once offlineimap is set up, just run `notmuch` from the command line to do some initial setup, which gets saved in `~/.notmuch-config` by default. You can set `~/mail` as your database path. notmuch has lots of options, the ability to set up various hooks and filters, and to sync certain IMAP markers with notmuch tags.

Here's a `~/.notmuch-config` which is roughly like the one I use:

    [database]
    path=/home/user/mail

    [user]
    name=First Last
    primary_email=me@domain.com

    [new]
    tags=new
    ignore=

    [search]
    exclude_tags=deleted;killed;spam;

    [maildir]
    synchronize_flags=true

## Initial PGP/MIME support

### Signing

The thread view panel will show the signature status of pgp-signed messages as reported by notmuch. notmuch tags them with te automatic tag `signed`.

Outgoing mail can be signed by setting lazarus.settings.gnupg_keyid to the id of the key which will be used to do the signing. It can be disabled/enabled on a per-message basis in the comopose view by pressing the 's' key (or the key that is mapped to the `toggle_pgp_sign` function).

You might also have to set lazarus.settings.gnupg_home.

### Encryption

Encrypted mail has the `encrypted` automatic tag set by notmuch. The latter is also responsible for the decryption. (`--decrypt=True` option of `notmuch show`)

Outging mail can be encrypted by toggling the `PGPEncrypt` flag in the compose panel (mapped to the 'e' key). It is encrypted using the public keys found in the GnuPG database of all reciepients. Recepients without a public key in the GnuPG dataabse will not be able to decrypt the message.

## More Screenshots

Searching:

![Search](/images/dodo-screen-search.png?raw=true)

Thread view:

![Thread](/images/dodo-screen-thread.png?raw=true)

Composition:

![Compose](/images/dodo-screen-compose.png?raw=true)

Various themes:

![Nord](/images/dodo-screen-nord.png?raw=true)
![Solarized Dark](/images/dodo-screen-solarized-dark.png?raw=true)
![Solarized Light](/images/dodo-screen-solarized-light.png?raw=true)


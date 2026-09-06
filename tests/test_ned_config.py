"""Tests for NED config generation and Notmuch/Maildir introspection."""

import os
import subprocess

from ned import config, settings


def test_init_config_fresh(tmp_path, monkeypatch):
    cfg_file = tmp_path / "ned" / "config.py"
    monkeypatch.setattr(config, "config_dir", lambda: str(tmp_path / "ned"))
    monkeypatch.setattr(config, "config_path", lambda: str(cfg_file))
    monkeypatch.setattr(config, "rules_path", lambda: str(tmp_path / "ned" / "rules.py"))

    # Mock notmuch config responses
    def mock_run(*args, **kwargs):
        if args == ("config", "get", "database.mail_root"):
            return subprocess.CompletedProcess(args, 0, stdout="/tmp/TestMail\n", stderr="")
        if args == ("config", "get", "user.name"):
            return subprocess.CompletedProcess(args, 0, stdout="Alice Smith\n", stderr="")
        if args == ("config", "get", "user.primary_email"):
            return subprocess.CompletedProcess(args, 0, stdout="alice@example.com\n", stderr="")
        if args == ("config", "get", "user.other_email"):
            return subprocess.CompletedProcess(args, 0, stdout="alice@gmail.com;\n", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr("ned.notmuch.run", mock_run)

    # Set up fake mail directories
    mail_root = tmp_path / "TestMail"
    (mail_root / "alice@example.com" / "Sent" / "cur").mkdir(parents=True)
    (mail_root / "alice@gmail.com" / "[Gmail]" / "Sent Mail" / "cur").mkdir(parents=True)
    (mail_root / "Archive" / "cur").mkdir(parents=True)

    def mock_run_with_mail(*args, **kwargs):
        if args == ("config", "get", "database.mail_root"):
            return subprocess.CompletedProcess(args, 0, stdout=f"{mail_root}\n", stderr="")
        return mock_run(*args, **kwargs)

    monkeypatch.setattr("ned.notmuch.run", mock_run_with_mail)

    path_written, backup_path = config.init_config()
    assert backup_path is None
    assert path_written == str(cfg_file)
    assert os.path.isfile(path_written)

    content = cfg_file.read_text()
    assert "SECTION 1: DERIVED MAIL AND ACCOUNT SETTINGS" in content
    assert "SECTION 2: DAEMON AND NETWORK SETTINGS" in content
    assert "SECTION 3: FILTER RULES" in content
    assert "USER EDITABLE:" in content
    assert "Alice Smith <alice@example.com>" in content
    assert "Alice Smith <alice@gmail.com>" in content
    assert "'gmail': None" in content

    # Check sample rules.py creation
    rules_file = tmp_path / "ned" / "rules.py"
    assert rules_file.is_file()
    assert "ned.settings.filter_rules" in rules_file.read_text()

    # Test that load_config executes and validates the generated file
    loaded_path = config.load_config()
    assert loaded_path == str(cfg_file)
    assert settings.email_address["alice"] == "Alice Smith <alice@example.com>"
    assert settings.email_address["gmail"] == "Alice Smith <alice@gmail.com>"
    assert settings.sent_dir["gmail"] is None
    assert "Sent" in settings.sent_dir["alice"]


def test_init_config_creates_timestamped_backup(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "ned"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.py"
    cfg_file.write_text("# Old custom config\nimport ned\nned.settings.log_level = 'DEBUG'\n")

    monkeypatch.setattr(config, "config_path", lambda: str(cfg_file))

    # Mock notmuch
    def mock_run(*args, **kwargs):
        if args == ("config", "get", "user.name"):
            return subprocess.CompletedProcess(args, 0, stdout="Bob Jones\n", stderr="")
        if args == ("config", "get", "user.primary_email"):
            return subprocess.CompletedProcess(args, 0, stdout="bob@example.com\n", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr("ned.notmuch.run", mock_run)

    path_written, backup_path = config.init_config()
    assert backup_path is not None
    assert os.path.isfile(backup_path)
    assert backup_path.endswith(".bak")
    assert ".20" in backup_path  # Contains date timestamp (e.g. .2026...)

    # Verify backup contains original content
    with open(backup_path, "r", encoding="utf-8") as f:
        backup_content = f.read()
    assert "# Old custom config" in backup_content

    # Verify new file contains derived sections
    new_content = cfg_file.read_text()
    assert "Bob Jones <bob@example.com>" in new_content
    assert os.path.basename(backup_path) in new_content


def test_user_edited_display_name_is_loaded(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "ned"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.py"
    monkeypatch.setattr(config, "config_path", lambda: str(cfg_file))

    # Simulate user custom display name in email_address
    cfg_file.write_text("""
import ned
ned.settings.smtp_accounts = ['work']
ned.settings.email_address = {'work': 'Chief Executive Officer <ceo@corp.com>'}
ned.settings.sent_dir = {'work': '~/Mail/work/Sent'}
""")

    path = config.load_config()
    assert path == str(cfg_file)
    assert settings.email_address["work"] == "Chief Executive Officer <ceo@corp.com>"


def test_init_config_preserves_existing_rules_py(tmp_path, monkeypatch):
    """init_config must not overwrite an existing rules.py."""
    cfg_dir = tmp_path / "ned"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.py"
    rules_file = cfg_dir / "rules.py"

    custom_rules_content = """# My custom rules
import ned
from ned.rules import Rule

ned.settings.filter_rules = [
    Rule(name='Custom', query='tag:alerts', tag_add=['urgent']),
]
"""
    rules_file.write_text(custom_rules_content)

    monkeypatch.setattr(config, "config_dir", lambda: str(cfg_dir))
    monkeypatch.setattr(config, "config_path", lambda: str(cfg_file))
    monkeypatch.setattr(config, "rules_path", lambda: str(rules_file))

    def mock_run(*args, **kwargs):
        if args == ("config", "get", "user.primary_email"):
            return subprocess.CompletedProcess(args, 0, stdout="alice@example.com\n", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr("ned.notmuch.run", mock_run)

    config.init_config()

    # Verify rules.py was preserved untouched
    assert rules_file.read_text() == custom_rules_content

    # Verify load_config loads the custom rules via Section 3 include
    config.load_config()
    assert len(settings.filter_rules) == 1
    assert settings.filter_rules[0].name == 'Custom'
    assert settings.filter_rules[0].query == 'tag:alerts'
    assert settings.filter_rules[0].tag_add == ['urgent']


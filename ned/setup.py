"""Build configuration for the standalone NED distribution.

NED (Notmuch Email Daemon) is the headless daemon part of the Lazarus
project, installable on its own with zero Qt dependencies:

    pipx install ./ned        # or: python -m pip install -e ./ned

The daemon reads configuration only from ~/.config/ned/config.py and
serves any client (the Lazarus desktop, the mobile PWA, or your own)
over HTTP on a Unix domain socket / Tailscale TCP.
"""
from pathlib import Path
import setuptools

_readme = Path(__file__).resolve().parent / "README.md"
if not _readme.is_file():
    _readme = Path(__file__).resolve().parent.parent / "README.md"

long_description = (
    _readme.read_text(encoding="utf-8")
    if _readme.is_file()
    else "Notmuch Email Daemon (NED) — headless notmuch index + Maildir mutate service"
)

if __name__ == "__main__":
    setuptools.setup(
        name="ned",
        version="0.3",
        author="Ruly Tafzil",
        description="Notmuch Email Daemon — headless notmuch index + Maildir mutate service",
        long_description=long_description,
        long_description_content_type="text/markdown",
        url="https://forge.rulytafzil.com/Home/lazarus",
        project_urls={
            "Bug Tracker": "https://forge.rulytafzil.com/Home/lazarus/issues",
        },
        classifiers=[
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
            "Operating System :: POSIX :: Linux",
        ],
        packages=["ned"],
        package_dir={"ned": "."},
        package_data={'ned': ['static/*']},
        install_requires=[],
        python_requires=">=3.10",
        entry_points={
            'console_scripts': [
                'ned=ned.main:main',
                'ned-client=ned.client:main',
            ]
        },
    )
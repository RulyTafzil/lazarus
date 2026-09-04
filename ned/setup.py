"""Build configuration for the standalone NED distribution.

NED (Notmuch Email Daemon) is the headless daemon part of the Lazarus
project, installable on its own with zero Qt dependencies:

    pipx install ./ned        # or: python -m pip install -e ./ned

The daemon reads configuration only from ~/.config/ned/config.py and
serves any client (the Lazarus desktop, the mobile PWA, or your own)
over HTTP on a Unix domain socket / Tailscale TCP.
"""
import setuptools

with open("../README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

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
import setuptools

# Read description from README.md
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Desktop integration (icons + .desktop entry) is intentionally NOT done
# via data_files: under pipx/venv installs those land in the environment's
# own share/ where no desktop ever looks.  The single install path is
# `lazarus --install-desktop` (see lazarus.app.install_desktop), which
# copies the bundled package icons into ~/.local/share.
#
# Three distributions, one repository:
#   - `pipx install .`          -> lazarus-mail: Qt desktop client, bundled
#                                  NED daemon, and the ned-client CLI.
#   - `pipx install ./ned`      -> ned: headless Notmuch Email Daemon and
#                                  ned-client alone, with zero Qt dependencies.
#   - `pipx install ./ned-mcp`  -> ned-mcp: Model Context Protocol server for AI
#                                  agents with account sandboxing and zero expunge.
# Pick any combination. The daemon is the same `ned` package in all cases.

setuptools.setup(
    name="lazarus-mail",
    version="0.3",
    author="Ruly Tafzil",
    description="A graphical, hackable email client based on notmuch",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://forge.rulytafzil.com/Home/lazarus",
    project_urls={
        "Bug Tracker": "https://forge.rulytafzil.com/Home/lazarus/issues",
    },
    license="GPL-3.0-or-later",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    packages=["lazarus", "ned"],
    py_modules=["ned_client"],
    package_data={
        'lazarus': ['icons/hicolor/*/apps/lazarus.png', 'theme_packs/*.json'],
        'ned': ['static/*'],
    },
    install_requires=["PyQt6>=6.2", "PyQt6-WebEngine>=6.2", "bleach>=5.0"],
    python_requires=">=3.10",
    entry_points={
        'console_scripts': [
            'lazarus=lazarus.app:main',
            'ned=ned.main:main',
            'ned-client=ned.client:main',
        ]
    },
)
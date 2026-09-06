"""Build configuration for the standalone ned-mcp distribution.

ned-mcp is the Model Context Protocol (MCP) server for NED, exposing
account-scoped, non-destructive email inspection and triage tools to AI agents.

Installable independently:
    pipx install ./ned-mcp
"""
from pathlib import Path
import setuptools

_readme = Path(__file__).resolve().parent / "README.md"
if not _readme.is_file():
    _readme = Path(__file__).resolve().parent.parent / "README.md"

long_description = (
    _readme.read_text(encoding="utf-8")
    if _readme.is_file()
    else "Model Context Protocol (MCP) server for Notmuch Email Daemon (NED)"
)

if __name__ == "__main__":
    setuptools.setup(
        name="ned-mcp",
        version="0.3",
        author="Ruly Tafzil",
        description="Model Context Protocol (MCP) server for Notmuch Email Daemon (NED)",
        long_description=long_description,
        long_description_content_type="text/markdown",
        url="https://forge.rulytafzil.com/Home/lazarus",
        project_urls={
            "Bug Tracker": "https://forge.rulytafzil.com/Home/lazarus/issues",
        },
        license="GPL-3.0-or-later",
        classifiers=[
            "Programming Language :: Python :: 3",
            "Operating System :: POSIX :: Linux",
        ],
        packages=["ned_mcp"],
        install_requires=[],
        python_requires=">=3.10",
        entry_points={
            "console_scripts": [
                "ned-mcp=ned_mcp.server:main",
            ]
        },
    )

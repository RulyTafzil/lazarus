# Workspace Guidelines

See [agent.md](file:///home/rulyt/Projects/lazarus/agent.md) for architectural reference and project documentation.

## Email Safety Policy

- All email operations must strictly go through the registered Model Context Protocol (MCP) tools provided by `clanker-email` or `personal-mail`.
- Never send, draft, delete, or inspect emails using bash commands, shell scripts, Python subprocesses, curl, or direct filesystem modifications.
- Never spawn ad-hoc `ned-mcp` processes in the terminal to override permissions defined in `mcp_config.json`.
- Strictly adhere to the account scoping and permission boundaries defined in `mcp_config.json`.
- Never attempt to send email from personal accounts (`contact`, `admin`, `gmail`) unless explicitly authorized and exposed with send permissions by the user in `mcp_config.json`.

# Project Instructions

- Do not add or preserve redundant backward-compatibility paths. When replacing an internal API, configuration shape, flag, request field, or UI contract, migrate current callers and remove old inputs, branches, constants, comments, and fallbacks in the same change unless explicitly requested otherwise.
- The development server has an autostart watcher. To reload it, kill the existing `server.py` process and wait briefly for it to come back; do not start a second server instance manually.
- In this workspace the sandbox can see the `server.py` process ID, but process control and localhost checks must be done outside the sandbox. Use escalated commands for killing `server.py` and for health checks such as `curl http://localhost:8000/` or API requests.

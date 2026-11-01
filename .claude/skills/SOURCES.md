# Installed skills and their sources (copied 2026-11-01)

| Skill dir | Skill name | Source repo | Commit | License |
|---|---|---|---|---|
| taste-skill | design-taste-frontend | https://github.com/Leonxlnx/taste-skill (skills/taste-skill) | 12af471 (2026-10-25) | MIT |
| web-design-guidelines | web-design-guidelines | https://github.com/vercel-labs/agent-skills (skills/web-design-guidelines) | 1a2c79d (2026-10-27) | MIT |
| awesome-design-md | awesome-design-md (wrapper written here) | https://github.com/VoltAgent/awesome-design-md (design-md/) | fcc7028 (2026-10-28) | MIT |
| image-to-code | image-to-code | https://github.com/Leonxlnx/imagegen-skills (skills/image-to-code) | beb16b0 (2026-10-19) | MIT |
| playwright-cli | playwright-cli | https://github.com/microsoft/playwright-cli (skills/playwright-cli) | 0a80807 (2026-10-31) | Apache-2.0 |

Runtime requirements:
- playwright-cli: run `npm install -g @playwright/cli@latest` once per new session/machine.
- taste-skill scripts: Node 22+ and Chrome/Chromium.
- web-design-guidelines: fetches rules from raw.githubusercontent.com at review time.

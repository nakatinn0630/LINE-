# Installed skills and their sources (copied 2026-11-01)

| Skill dir | Skill name | Source repo | Commit | License |
|---|---|---|---|---|
| taste-skill | design-taste-frontend | https://github.com/Leonxlnx/taste-skill (skills/taste-skill) | 12af471 (2026-10-25) | MIT |
| web-design-guidelines | web-design-guidelines | https://github.com/vercel-labs/agent-skills (skills/web-design-guidelines) | 1a2c79d (2026-10-27) | MIT |
| awesome-design-md | awesome-design-md (wrapper written here) | https://github.com/VoltAgent/awesome-design-md (design-md/) | fcc7028 (2026-10-28) | MIT |
| image-to-code | image-to-code | https://github.com/Leonxlnx/imagegen-skills (skills/image-to-code) | beb16b0 (2026-10-19) | MIT |
| playwright-cli | playwright-cli | https://github.com/microsoft/playwright-cli (skills/playwright-cli) | 0a80807 (2026-10-31) | Apache-2.0 |
| lemo-opuscar | lemo-opuscar | https://github.com/lemomo-ai/lemo-opuscar (plugin/skills/lemo-opuscar) | 3a7132b (2026-11-12) | MIT (repo root LICENSE; third-party assets keep their own terms) |
| mg-styles-15 | mg-styles-15 (wrapper written here) | https://github.com/Vincentwei1021/mg-styles-15 (prompts/, harness/, template.md, rubric.md) | 269f6ed (2026-11-09) | MIT |
| claude-animation-base | claude-animation-base (wrapper written here) | https://github.com/JohnHeibel/ClaudeAnimationBase (all except docs/emotions.webp) | 0ac8bf2 (2026-09-24) | MIT |
| pdoom-video | pdoom-video (pointer only, no files copied) | https://github.com/JohnHeibel/PDoomVideo | fa546a3 (2026-09-25) | none stated |
| awesome-opus5-5-videos | awesome-opus5-5-videos (wrapper written here) | https://github.com/yihui-dev/awesome-opus5-5-videos (README, prompts/, data/) | 92aee2f (2026-10-13) | MIT |
| awesome-claude-5-5-videos | awesome-claude-5-5-videos (wrapper written here) | https://github.com/athemeroy/awesome-claude-5-5-videos (README, docs/, data/) | 87e5cbf (2026-10-11) | CC BY 4.0 |

Runtime requirements:
- playwright-cli: run `npm install -g @playwright/cli@latest` once per new session/machine.
- taste-skill scripts: Node 22+ and Chrome/Chromium.
- web-design-guidelines: fetches rules from raw.githubusercontent.com at review time.
- lemo-opuscar: on first use `scripts/setup.sh` clones the style library to `~/lemo-opuscar`; needs Node 20+, ffmpeg 7+ and Python 3.11+ (or uv).
- mg-styles-15, claude-animation-base: Node, Chrome/Chromium and ffmpeg to render.

The six video skills above (lemo-opuscar to awesome-claude-5-5-videos) were copied 2026-11-14.

---
name: pdoom-video
description: Reference to JohnHeibel/PDoomVideo, the full source of a 156-second hand-painted music video made in code (p5.js + p5.brush, nine chapters synced to lyrics, with a storyboard and an animation guide). Use when the user wants to study or follow that music-video pipeline (lyrics-timed chapters, storyboard first, parallel subagents), or asks for "PDoomVideo".
---

# PDoomVideo (reference only)

Upstream: https://github.com/JohnHeibel/PDoomVideo

No upstream files are copied here: at commit fa546a3 (2026-09-25) the repository has no licence file, and it includes a song (`assets/pdoom.mp3`) whose rights are not stated. To read it, clone it outside this repository:

```sh
git clone --depth 1 https://github.com/JohnHeibel/PDoomVideo.git
```

What to read there:
- `STORYBOARD.md`: the shot-by-shot plan.
- `ANIMATION_GUIDE.md`: the style and code guide written for subagents.
- `src/ch/`: the nine chapters, one file each; `src/` holds the shared character, props, lyrics and timeline.
- `render.mjs`, `studio.html`: frames painted in headless Chrome, encoded with ffmpeg.

For a new film, use the `claude-animation-base` skill instead: it is the same author's MIT-licensed starter kit built from this video's code.

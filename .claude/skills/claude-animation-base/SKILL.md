---
name: claude-animation-base
description: Starter kit for hand-painted cartoon animation made in code with p5.js + p5.brush (JohnHeibel/ClaudeAnimationBase), with the Clawd character, 31 acted emotions, a headless renderer and an animation guide. Use when the user wants a character animation, cartoon short or music-video-style film in a watercolour / brush-stroke look, or asks for the "Animation Base".
---

# Claude Animation Base

Files here are copied from https://github.com/JohnHeibel/ClaudeAnimationBase (MIT). This wrapper was written in this repository; it is not part of upstream.

1. Copy this skill's folder (everything except `SKILL.md`) into a new project folder; don't build the film inside `.claude/skills`.
2. Read `ANIMATION_GUIDE.md` first and follow it: storyboard, build shot by shot in `src/scenes/`, render contact sheets to check the work, then write `out/video.mp4`.
3. Render: `npm install`, then `node render.mjs --clip --out=out/video.mp4`. Needs Node.js, Chrome/Chromium and ffmpeg. Without a GPU add `--soft-gl` (slow on watercolour fills); pass `--chrome=<path>` or set `CHROME_PATH` if Chrome is not found.

`docs/emotions.jpg` and `docs/views.jpg` are the model sheets. The animated `docs/emotions.webp` (6.7 MB) was not copied; it is in the upstream repository.

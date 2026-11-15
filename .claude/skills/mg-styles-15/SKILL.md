---
name: mg-styles-15
description: 15 motion-graphics (MG) style prompts from Vincentwei1021/mg-styles-15 (flat vector, line art, isometric, 3D render, cel boil, collage, liquid, shape morph, Bauhaus, synthwave, aurora glass, variety captions, sticker explainer, pixel art, cyberpunk HUD), each a full prompt for a 10-second film made by writing code. Use when the user wants a short motion-design video or animation in one of these styles, asks which MG styles exist, or wants a prompt/template/rubric for a code-rendered film.
---

# MG Styles 15

Files here are copied from https://github.com/Vincentwei1021/mg-styles-15 (MIT). This wrapper was written in this repository; it is not part of upstream.

- `prompts/<nn>-<style>.md`: the full prompt for each of the 15 styles. Pick the one that matches the request and adapt its subject.
- `template.md`: the shared prompt skeleton for writing a new style.
- `rubric.md`: the review rubric used to score and revise a film.
- `harness/render.mjs`, `harness/preview.html`: the renderer. A film is a web page exposing `window.renderAt(t)`; the harness captures frames in headless Chrome and ffmpeg joins them. Needs Node, Chrome/Chromium and ffmpeg; `package.json` and `requirements.txt` list the packages.
- `README.md`: upstream overview and the style table.

Not copied (too large for this repo): `demos/` (each film's full source), `lib/audio` (instrument samples used by each film's `audio.py`), `videos/`. When a film needs them, clone upstream: `git clone --depth 1 https://github.com/Vincentwei1021/mg-styles-15.git`.

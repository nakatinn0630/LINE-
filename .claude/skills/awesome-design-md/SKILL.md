---
name: awesome-design-md
description: Library of 84 ready-made DESIGN.md design-system files (VoltAgent/awesome-design-md) analyzed from real websites such as Stripe, Linear, Vercel, Notion, Apple and Claude. Use when the user wants a UI, page or app to look like a specific brand or site, asks for a DESIGN.md, or wants a consistent visual system (colors, typography, spacing, components) to build from.
---

# Awesome DESIGN.md

DESIGN.md is a plain-markdown design-system document (concept from Google Stitch) that coding agents read to produce visually consistent UI.
Each folder under `design-md/<site>/` in this skill holds a `DESIGN.md` (and a README) describing that site's tokens, typography, color, layout and component rules.

## How to use

1. Pick the site whose look the user wants. Available: 1win, airbnb, airtable, aljazeera, apple, binance, bmw, bmw-m, bugatti, cal, claude, clay, clickhouse, cohere, coinbase, composio, cursor, dell-1996, dji, elevenlabs, expo, ferrari, figma, framer, hashicorp, hp, ibm, intercom, jobright, kraken, lamborghini, linear.app, lovable, mastercard, mercury, meta, minimax, mintlify, miro, mistral.ai, mongodb, mydealz, nerdwallet, nike, nintendo-2001, notion, nvidia, ollama, opencode.ai, oracle, oura, pinterest, playstation, posthog, raycast, renault, replicate, resend, revolut, runwayml, sanity, sap, sentry, shopify, slack, spacex, spotify, starbucks, stripe, supabase, superhuman, tesla, theverge, together.ai, uber, vercel, vodafone, voltagent, warp, webflow, wired, wise, x.ai, zapier.
2. Read `design-md/<site>/DESIGN.md` from this skill's directory.
3. Either copy it to the project root as `DESIGN.md` (when the user wants it as the project's design system) or apply it directly while building.
4. Follow its tokens and rules for every component you generate; do not mix in defaults that contradict it.
5. If no listed site fits, say so and offer the closest matches instead of inventing one.

Note: these files describe other companies' visual styles for inspiration. Do not reproduce their logos, trademarks or brand names in the user's product.

Source: https://github.com/VoltAgent/awesome-design-md (MIT, see LICENSE).

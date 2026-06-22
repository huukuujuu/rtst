# RTST Worklog

## Current Baseline Summary

This file tracks what has been completed so far and what Codex changes while the user is away.

### Completed Before Autonomous Work

- Built a Python/PySide desktop app for real-time subtitle translation overlays.
- Added two subtitle sources:
  - `screen_ocr`: captures a user-selected subtitle region and runs OCR.
  - `browser_dom`: reads subtitle text exposed through Chrome DevTools, including `video.textTracks.activeCues`, common subtitle DOM nodes, shadow DOM, and iframe targets.
- Added OpenAI Platform API key translation mode.
- Added ChatGPT/Codex OAuth mode based on the OpenClaw-style flow:
  - OAuth Authorization Code + PKCE.
  - `auth.openai.com` login.
  - `chatgpt.com/backend-api/codex/responses` translation call.
  - Token refresh and local token storage.
- Added one-click batch launchers:
  - `run_rtst_oauth.bat`
  - `run_rtst_browser_dom.bat`
- Reworked the UI into tabs:
  - `Run`, `History`, `Source`, `Translation`, `Overlay`, `Advanced`.
- Added draggable overlay positioning.
- Added overlay accumulation so recent translated subtitles stack instead of replacing each other one by one.
- Added overlay width and max-height controls.
- Added chat-like translation history with source/translation pairs.
- Added visual subtitle-change detection for screen OCR mode instead of exposing a manual capture interval as a primary UI setting.
- Added browser DOM filtering so hover UI, player titles, control bars, menus, and tooltips are less likely to be translated as subtitles.

### Known Constraints

- `browser_dom` can only read subtitles that are exposed through DOM or `textTracks.activeCues`.
- DRM-only, canvas-only, or closed internal player subtitles cannot be read without unsupported capture bypassing.
- Codex OAuth mode depends on ChatGPT/Codex backend behavior and model/account permissions, so it may change outside this app.
- Translation latency is still limited by network and model response time.

## Autonomous Work Branch

- Base protection strategy: initialize a local Git repository if none exists, commit the current baseline, then continue on a separate branch.
- Branch name: `codex/autonomous-improvements-20260623-011159`

## Autonomous Work Log

### 2026-06-23

- Started autonomous maintenance session requested by the user.
- Added `.tmp_openclaw/`, logs, temporary files, and cache folders to `.gitignore` so baseline and future branches do not capture secrets or bulky runtime artifacts.
- Created this worklog to keep progress, implementation notes, and validation results in one place.
- Initialized a local Git repository because the project did not have one yet.
- Committed the current implementation as baseline commit `566c183` on `main`.
- Created and switched to `codex/autonomous-improvements-20260623-011159` for all unattended changes.
- Improvement 1: added a configurable translation history retention limit.
  - Added `translation_history_limit` to settings with a safe range of 20 to 1000.
  - Added a `Max` control in the `History` tab.
  - Trimmed older history entries automatically so long unattended sessions do not slow the UI indefinitely.
  - Updated README, SPEC, and config tests.
- Improvement 2: tightened browser DOM subtitle fallback extraction.
  - Kept `textTracks.activeCues` as the first-choice source.
  - Skipped parent subtitle containers when visible child subtitle nodes are available.
  - This reduces duplicate source text and repeated translations on players that expose both caption windows and caption segments.
  - Updated README, SPEC, and browser DOM tests.
- Improvement 3: improved automatic Chrome tab selection for browser DOM mode.
  - Added a DevTools probe that scores debuggable pages by visible video elements, active text tracks, subtitle DOM nodes, and media iframes.
  - Kept `Browser tab filter` as the highest priority when the user sets it.
  - This should reduce cases where the app connects to a blank/new tab and appears to do nothing.
  - Updated README, SPEC, and browser DOM tests.

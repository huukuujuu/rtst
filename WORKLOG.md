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
- Improvement 4: reset the duplicate-detection baseline when no subtitle text is visible.
  - When OCR/DOM returns empty text, `last_source_text` is cleared.
  - This allows the same subtitle text to be shown again after a real gap, usually through the translation cache rather than a new API call.
- Improvement 5: strengthened translation instructions for noisy subtitle extraction.
  - Shared one subtitle-translation instruction helper across OpenAI API key mode and Codex OAuth mode.
  - Replaced OCR-only wording with subtitle text wording that covers OCR and DOM extraction.
  - Added explicit guidance to ignore accidental player UI text such as titles, buttons, menus, timestamps, control labels, and tooltips.
  - Added a regression test for the instruction content.
- Improvement 6: added `.gitattributes` for predictable line endings.
  - Python, Markdown, JSON, and text-like files use LF.
  - Windows batch files use CRLF.
  - This reduces noisy Git line-ending warnings during future work.
- Improvement 7: cleaned noisy DOM subtitle text based on observed logs.
  - Found repeated phrases and player timeline text such as `0:02 / 19:45 Intro` in `rtst_debug.log`.
  - Added DOM-only post-processing to remove media progress text and safe common chapter labels such as `Intro`.
  - Added phrase-level repetition collapse for cases where the same subtitle phrase is duplicated two or three times.
  - Kept short emphasis such as `no no no` unchanged.
  - Updated README, SPEC, and browser DOM tests.
- Improvement 8: changed the overlay from a latest-subtitle surface into a translation history overlay.
  - Removed the separate in-memory overlay entry list and now render the overlay from `translation_history`.
  - The overlay shows the latest translated history items as a compact chat-like panel.
  - Removed the redundant `Accumulate subtitles` UI path; overlay history is now the default behavior.
  - Added direct bottom-right corner resizing for the overlay and persist the resized width/height to settings.
  - Updated README and SPEC to describe the history overlay behavior.
- Improvement 9: added overlay on/off and scrollable history behavior.
  - Added `overlay_enabled` to settings and a `Show overlay` checkbox in the Overlay tab.
  - Replaced the overlay's static label with a read-only scrollable text panel.
  - New translations still scroll the overlay to the newest entry, but older entries remain reachable by scrolling inside the overlay panel.
  - Kept direct resizing through the bottom-right size grip.
- Improvement 10: changed the overlay to show the full retained translation history.
  - Removed the `Overlay history` UI limit.
  - The overlay now renders every entry retained by `translation_history`, matching the History tab's current retention window.
  - `History > Max` is the single control for how much history is retained overall.
- Improvement 11: widened DOM subtitle position filtering for top-positioned captions.
  - Replaced the lower-half-only DOM caption band with a full video-height band plus a small tolerance.
  - This allows subtitle elements rendered near the top of the video while keeping horizontal video-overlap and player-control filtering.
  - Added a regression test so the old `videoRect.height * 0.45` lower-bound heuristic does not come back unnoticed.
- Improvement 12: softened translation style without adding runtime context.
  - Updated the translation prompt from a generic translation-engine instruction to a subtitle-localizer instruction.
  - Added explicit guidance to translate idioms, phrasal verbs, slang, jokes, and implied meanings by meaning rather than word-for-word.
  - Kept the request single-subtitle-only so realtime latency is not increased by extra context.
- Improvement 13: show subtitle source text before translation completes.
  - Added a source-detected worker signal so the UI can render the original subtitle immediately after OCR/DOM extraction.
  - History and overlay entries now start as pending source entries and are completed in place when the translation arrives.
  - Pending overlay entries show the source text even when the normal source-display option is off, then switch back to the configured display once translated.
- Improvement 14: adjusted compact overlay defaults.
  - Changed default overlay width and height to 600 px.
  - Changed default overlay font size to 15 px.
  - Added a config regression test for the compact defaults.

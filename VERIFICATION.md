# Verification — 2026-10-12

Executed on macOS against version 0.2.0. Test state and exports were isolated from the artist's application data. Originals and older exports were preserved.

## Automated processing checks

`./verify.sh` passed: **27 pytest cases**, real loopback HTTP import/export, CLI success and missing-input failure, collected-file integrity, and clean shutdown. JavaScript syntax checks and `git diff --check` also passed.

Coverage includes:

- Native print dimensions, binary PNG/TIFF, preserved 600 DPI, fallback DPI, and proportional DPI for integer enlargement.
- Separate 85-megapixel input and 200-megapixel print limits. A 6602 × 5088 scan permits 2× and rejects 3×; rejection occurs before a batch/run is created.
- Source hashes unchanged after exports; independent print, regular, and stylized stages.
- Exact Red Sun black-and-white parity when the sibling reference is present. Independent 512/1024 artwork grids and blank/native values. Styled exports and preview thumbnails contain no gray pixels.
- One numbered summary per version, deterministic duplicate names, byte-identical flat social copies, and exclusion of marked exports from discovery.
- Uncertain estimates export and flag review; a damaged input does not stop another comic.
- Stage hash repair, preservation of successful exports when a replacement fails, cancellation/resume, and interrupted queue recovery in the existing run.
- Explicit correction persistence, fingerprint reuse after renaming, coordinate-version invalidation, source-change invalidation, cached cleanup for draft previews, legacy settings/history, and HTTP guards.
- Single-comic regeneration preserving the full-page hash and modification time while removing obsolete social slides and refreshing collected copies.

The only pytest warnings were upstream Starlette/AnyIO test-client deprecations.

## Real browser checks

`tests/browser_workflow.mjs` ran through the Codex in-app browser using actual pointer/keyboard input and DOM locators: **31 checks passed**. No app-internal state was used to drive interactions.

Checks cover selection/body movement, every edge/corner resize handle, explicit drawing, deletion, reading order, one/ten-pixel nudges, zoom, pan, undo/redo, restoration of detection, coordinate editing before save, save/reopen, draft cancellation, regeneration/reopen, and actual crop previews. Image/SVG bounds match before and after panning, scrolling, and resizing the window to 1100 × 780. Temporary viewport changes were reset. Blank panel/summary grid settings also saved and reopened correctly before restoring 512/1024.

To repeat the browser suite, launch the app with isolated `COMIC_MAKER_DATA`, import and export a two-panel `fixture.png`, then invoke the exported function with a cua_repl tab and its browser's viewport capability. `scripts/verify.py` shows how to construct the fixture. This browser suite requires the Codex browser runtime and is separate from `verify.sh`.

The browser run exposed a numeric-coordinate commit issue. Coordinates now commit on blur and explicit save; the suite checks that the SVG changes before saving and that reopening retains the correction.

## Seven supplied scans

All seven raw TIFFs completed native-resolution production. Print masters were 6602 × 5088, binary, with 600 DPI. Source hashes remained unchanged. The output run contained **56 collected social files**.

| Scan | Panels | Collected slides |
| --- | ---: | ---: |
| 09.08.2026 COMICS | 4 | 10 |
| 09.15.2026 COMICS 1 | 1 | 4 |
| 09.15.2026 COMICS 2 | 3 | 8 |
| 09.15.2026 COMICS | 2 | 6 |
| 09.20.2026 COMICS 1 | 4 | 10 |
| 09.20.2026 COMICS 2 | 4 | 10 |
| 09.20.2026 COMICS | 3 | 8 |

Inspected source/cleaned comparisons and lettering/border patches at native resolution across all seven scans, plus regular and stylized summaries. Sampled lettering, punctuation, speech bubbles, and fine strokes retained their hand-drawn shapes. Summary previews showed complete frames and preserved reading order. The automatic layouts for the three-panel scans use a vertical stack; review retains layout overrides. Visual inspection was of sampled native regions and complete summary compositions, not every individual pixel.

## GUI acceptance on a supplied comic

Processed `09.08.2026 COMICS.tiff` through the GUI. Reopened its completed result and changed panel 1 from x=575, width=1377 to x=563, width=1401, retaining y=1623 and height=1757. Saved and regenerated in the same run, then reopened the saved boundaries.

Confirmed:

- The saved source-coordinate correction reopened correctly.
- The print crop became 5601 × 1822; its file hashes changed.
- Both carousels' affected file hashes changed.
- All ten collected social copies matched the regenerated outputs.
- Full-page print hashes and modification time remained unchanged.

A separate fixture test reduced two panels to one and confirmed old panel/summary files disappeared, leaving four collected slides. Cancelling a subsequent draft left the saved correction intact.

## Scope of evidence

macOS and the in-app browser were exercised. Windows/Linux native launchers were not tested on their operating systems. Enlargement dimensions/DPI and the supplied-scan 2× limit were checked; the seven-scan visual review used native print exports. Frame edits adjust crop regions and do not redraw ink borders.

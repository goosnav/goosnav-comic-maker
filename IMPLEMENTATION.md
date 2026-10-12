# Implementation contract — version 0.2.0

## Processing

`imaging.py` loads originals, preserves DPI, and performs full-resolution ink cleanup. `detection.py` retains the existing bordered-panel detector, local recovery, and reading order. `layout_base.py` selects and places summary panels. `red_sun.py` contains the adapted black-and-white grid reduction; siblings are not imported at runtime.

`pipeline.py` is the shared GUI/CLI service. Schema 2 records independent recipes and file hashes for full-page print, print crop, regular carousel, and stylized carousel. Print enlargement is binary nearest-neighbor, with proportional DPI. Social canvases are 2160 × 2700. Regular fitting uses Lanczos; stylized fitting uses nearest-neighbor. Both summary and individual stylized artwork start from cleaned native crops.

Each GUI batch creates a numbered run and stable, collision-free comic names. Completed phases are hash-checked on retries. Social directories are replaced through prepared staging directories with recoverable backups. Print files are prepared before replacement. Collected social copies are derived outputs and can be repaired independently. Existing schema 1 exports are readable and left unchanged.

## Queue and corrections

`jobs.py` retains the SQLite queue and single image worker. Jobs persist their run location and per-comic status. Restart recovery requeues unfinished schema 2 items. Regeneration queues only the requested comic within the original run. Failures and uncertain estimates remain visible without blocking the rest of a batch.

Preview preparation caches the deskewed full-resolution scan and cleanup. Crop-preview requests read those cached pixels and never save corrections. Corrections have a source SHA-256 and coordinate version. Saving checks that the current source matches the preview; reading ignores obsolete corrections.

## Interfaces

- `POST /api/preview`: prepare/read a preview and saved boundaries; optional boxes are a draft.
- `POST /api/preview/render`: generate crop and slide previews from cached cleanup.
- `GET /api/corrections/{source}`: read a valid saved correction.
- `POST /api/corrections/{source}`: explicitly save validated boundaries from a matching preview.
- `POST /api/jobs/{job}/regenerate`: queue one comic in the existing run, optionally overriding summary layout.

Results include stage completion, review status, output hashes, and collected-social location. Server guards and native file pickers remain in place.

## User interface

`app.js` manages selection, settings, and the batch list. `editor.js` owns drafts, history, source coordinates, selection, resizing, drawing, panning, and preview request ordering. The image and SVG occupy the same image-only stage. Coordinates come from the inverse SVG screen transform; handles and labels scale to remain usable on screen.

Only explicit save actions persist drafts. Save & regenerate uses the saved correction and the selected batch's settings. Result image URLs include output hashes so corrected summaries cannot remain stuck in the browser cache.

## Acceptance

The verification record covers print dimensions/DPI and limits, immutable sources, separate output stages, independent grids, summary ordering, collision-free collected copies, cancellation/restart, explicit corrections, GUI interactions, and a completed comic corrected through the GUI and regenerated in place. Frame editing controls crop regions, not visible ink strokes.

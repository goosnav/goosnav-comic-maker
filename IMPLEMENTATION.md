# Delivery contract

For a comic artist, turn folders of pencil scans into crisp finished comics and ordered social carousel PNGs through one local browser application. Optional beta colorization produces a second matching set.

The normal workflow is choose folders, choose a saved look, choose an export folder, and process. Local processing works without credentials. One image is one comic. Sources remain untouched. A failure affects one stage or comic, never the whole queue.

## Acceptance evidence

- Launch: `./run.command` opens the local browser GUI.
- Image quality: binary output contains only black and white; color output contains only its shared flat palette; resizing cannot add gray fringes.
- Real samples: all seven supplied raw TIFFs are processed, with panel counts and lettering inspected against the supplied edited references.
- Batch: nested folders, duplicate names, damaged images, cached reruns, refresh, cancellation, and interrupted-run recovery.
- Export: finished comic, source-layout master, each carousel slide, full-comic summary, settings/provenance manifest, optional color set.
- GUI: import, preview, settings, processing, results, correction, and reopen through a real browser.
- AI: deterministic fixture proves integration without network; mocked provider contracts and recovery tests; live results are reported separately.
- Verification: `./verify.sh` composes tests and a real HTTP/CLI smoke in temporary storage.

## Decisions

- Python 3.12, FastAPI, Pillow, NumPy, OpenCV, HTTPX, SQLite, plain HTML/CSS/JavaScript. Shared services for GUI and CLI.
- Copy useful image/detection code into this project; sibling apps are not runtime dependencies.
- Clean ink is the default; Paint has a separate detail-scale control. Both have hard edges, no dithering. No original-line multiply overlay on AI results.
- Rectangular bordered panels are the primary case. Local recovery precedes optional vision recovery. Uncertain results export best candidates with a warning; no candidates use one whole-art crop.
- Default social canvas is 1080 × 1350, with a 2160 × 2700 option. Summary reflows the panels in reading order, using the existing layout scoring.
- GUI is a monochrome print-workshop surface: Helvetica system text, Times editorial text, sharp borders, generous preview space.
- macOS is the native acceptance platform. Windows/Linux launch scripts are supplied but must not be described as tested without evidence.

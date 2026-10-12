# Goosnav Comic Maker

Turn pencil scans into print masters and two social carousels. All image processing runs locally. Originals are never modified.

Double-click **run.command** on macOS, or run `./run.sh`. First launch needs uv and internet access to install the private Python environment. Later processing works offline. Windows and Linux launchers are included; native acceptance was performed on macOS.

## Daily use

1. **Choose folder** or **Choose files**. Set **Include subfolders** as needed. You can also paste paths.
2. Choose a destination and press **Process comics**.
3. Use **Open exports** or **Open all social** to collect the results.

Each batch creates a new numbered run. Failed comics do not stop the others. Estimated boundaries export with a **Needs review** flag. Refreshing the browser does not interrupt a batch; restarting the app resumes interrupted work. **Stop** takes effect between processing operations. **Retry unfinished** preserves completed stages whose output hashes still match.

## Print and social quality

- **Print:** cleaned full page and comic-only crop, each as lossless PNG and TIFF. Both contain black ink and white paper at full source resolution. The crop encloses accepted panel boundaries with a small margin.
- **DPI:** preserve source DPI, or use 600 when missing. Optional integer print enlargement increases DPI proportionally, preserving physical print size. It enlarges existing pixels; it cannot reconstruct missing scan detail.
- **Regular carousel:** one 2160 × 2700 slide per panel, followed by a summary. High-quality resizing retains fine strokes.
- **Stylized carousel:** Red Sun black-and-white processing applied separately to full-resolution cleaned panel crops. Defaults are 512 pixels across each panel's artwork and 1024 across each summary panel's artwork. Final fitting uses nearest-neighbor resizing without dithering or gray fringes.

Occasional controls live in **Settings**: ink strength, dust removal, print enlargement, the two artwork grid widths, and filename filtering. Leave a grid width blank to keep native pixels before fitting. Print masters always use the full-resolution cleanup, independent of stylization.

Inputs are limited to 85 megapixels. Enlarged print outputs have a separate 200-megapixel limit. Oversized enlargement is rejected before a run starts. The supplied 6602 × 5088 scans support 2× print enlargement.

## Edit panels

Open **Edit panels** on an imported comic or a completed result. Saved boundaries load immediately. The editor uses full-resolution coordinates in the deskewed scan.

- **Select** is the default. Drag a panel body to move it; drag any of eight handles to resize it.
- Use **Add panel** to draw a new region. Delete and reorder panels with the sidebar controls.
- Use **Fit**, **100%**, **Zoom to panel**, and **Pan**. Hold Space to pan or Ctrl/Cmd while scrolling to zoom.
- Arrow keys nudge one source pixel; Shift + arrow nudges ten. Delete/Backspace deletes the selected panel. Ctrl/Cmd + Z undoes; Ctrl/Cmd + Shift + Z or Y redoes.
- Edit coordinates directly. Crop, regular, stylized, and summary previews update from cached cleanup.
- **Save boundaries** commits corrections. **Cancel**, Close, and Escape discard unsaved edits. **Restore automatic detection** changes the draft.
- **Save & regenerate** updates only that comic in its existing run. A boundary-only correction leaves full-page print masters unchanged, replaces affected outputs, removes obsolete slides, and refreshes collected social copies. On an imported comic, **Save & process** starts a new run.

Frame editing changes crop regions. It does not redraw visible border lines. Corrections are tied to source content and coordinate version; a changed source invalidates them.

## Export folders

```text
comic-maker-run-001/
  Comic A/
    print/
      full-page.png
      full-page.tif
      comic.png
      comic.tif
    regular-carousel/
      01-panel.png
      02-panel.png
      03-summary.png
    stylized-carousel/
      01-panel.png
      02-panel.png
      03-summary.png
    manifest.json
  Comic A-2/
    ...
  all-social/
    Comic A--regular-carousel--01-panel.png
    Comic A--regular-carousel--02-panel.png
    Comic A--regular-carousel--03-summary.png
    Comic A--stylized-carousel--01-panel.png
    ...
  batch-<id>.json
```

The summary is saved once in each version, numbered after its panels. `all-social` contains byte-identical copies of every social slide. Prefixes distinguish comics and versions; numbers preserve reading order. Duplicate comic names receive deterministic suffixes. Marked export folders are excluded from recursive discovery.

Manifests and recipes use version 2, with completed stages and output hashes. Replacement files are prepared before successful exports are replaced. Older exports remain untouched and accessible through history. Saved cleanup preferences migrate; obsolete AI and exclusive-style preferences are discarded. AI colorization, vision recovery, providers, and request accounting have been removed.

## Local state and CLI

Settings, correction records, previews, and SQLite batch history live outside the repository:

- macOS: `~/Library/Application Support/GoosnavComicMaker`
- Windows: `%LOCALAPPDATA%/GoosnavComicMaker`
- Linux: `$XDG_DATA_HOME/GoosnavComicMaker` or `~/.local/share/GoosnavComicMaker`

Set `COMIC_MAKER_DATA` for isolated work. The server binds to loopback and checks Host, Origin, and a session token for mutations. Sibling apps are reference sources, not runtime dependencies.

```sh
./run.sh batch "/path/to/scans" --out "/path/to/exports" --json
./run.sh batch "/path/to/scans" --out "/path/to/exports" --pattern '*raw*'
./run.sh batch "/path/to/scans" --out "/path/to/exports" --settings settings.json
```

GUI and CLI share the same processing service. CLI runs create new numbered folders; automatic restart recovery belongs to the GUI queue. Exit code 0 means all images completed; 2 means missing inputs or incomplete results.

## Verification

Run `./verify.sh` for processing, metadata, recovery, HTTP, CLI, and shutdown checks. Real browser interactions are implemented in `tests/browser_workflow.mjs`. See `VERIFICATION.md` for the executed checks and visual inspection record.

The detector targets mostly rectangular bordered comics. Use one image per comic; split animated images and multi-page TIFFs before import. PDF and PSD inputs are not supported.

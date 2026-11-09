# Goosnav Comic Maker

Turn pencil scans into print masters and two social carousels. All image processing runs locally. Originals are never modified.

Double-click **run.command** on macOS, or run `./run.sh`. First launch needs uv and internet access to install the private Python environment. Later processing works offline. Windows and Linux launchers are included; native acceptance was performed on macOS.

## Daily use

1. **Choose folder** or **Choose files**. Set **Include subfolders** as needed. You can also paste paths.
2. Choose an **Export** mode, check the destination (the saved destination is ignored unless you picked one yourself, so the run always lands in your source folder by default) and press **Process comics**. It defaults to the first folder you choose (or the folder holding the first file), so the numbered run lands inside it. Choosing a destination yourself overrides this.
3. Use **Open exports**, **Open all social**, **Open book prints**, **Open processed pages**, or **Open full-size pages** to collect the results.

The main screen shows active processing and the most recent batch started there. **Export history** opens past batches in a separate tab, including their export, retry, and panel editing actions.

Each batch creates a new numbered run. Failed comics do not stop the others. Estimated boundaries export with a **Needs review** flag. Refreshing the browser does not interrupt a batch; restarting the app resumes interrupted work. **Stop** takes effect between processing operations. **Retry unfinished** preserves completed stages whose output hashes still match.

## Export modes

- **Full:** per-comic folders, all collection folders, and manifests (the previous layout).
- **Batch:** only the five collection folders: book crops, PNG/TIFF pages, original-format filtered pages, flat social images, and carousel folders.
- **Minimal:** only `all-social-carousels` and `all-full-size-pages`.
- **Single folder:** choose filtered pages, carousel folders, summary slides only, book crops, PNG/TIFF pages, or flat social images.

Every mode creates a new numbered run beneath the destination. The selection is remembered for the next batch. Compact modes keep manifests and working files in local app storage, so their exports contain only the selected folders. Editing and retrying use the original batch's mode. Unselected processing stages are skipped. Print enlargement does not affect the original-format filtered pages.

**Single folder → Summary slides only (4:5)** creates `all-social-summaries` with one Red Sun portrait summary per comic, named after the comic. Each summary uses the existing automatic panel layout, summary grid setting, and 2160 × 2700 canvas. This option skips individual slides, ads, captions, and print exports.

For posting, choose **Single folder → Social carousel folders**, then drag the child folders from `all-social-carousels` into Drive:

```text
all-social-carousels/
  stylized-carousel-2026-11-02_1032-01/
    image-1.png
    image-2.png
    image-3.png      (ad, if enabled)
    image-4.png      (summary)
    stylized-carousel-2026-11-02_1032-01.txt
```

The caption filename is the exact name of its containing carousel folder plus `.txt`. Slides are numbered without padding or descriptions. The summary is last; an enabled ad is second to last. Caption files are created when a caption source is selected. Existing collected carousels are brought into this format when the app opens.

## Print and social quality

- **Print:** cleaned full page and comic-only crop, each as lossless PNG and TIFF. Both contain black ink and white paper at full source resolution. The crop encloses accepted panel boundaries with a small margin.
- **DPI:** preserve source DPI, or use 600 when missing. Optional integer print enlargement increases DPI proportionally, preserving physical print size. It enlarges existing pixels; it cannot reconstruct missing scan detail.
- **Regular carousel:** one 2160 × 2700 slide per panel, followed by a summary. High-quality resizing retains fine strokes.
- **Stylized carousel:** Red Sun black-and-white processing applied separately to full-resolution cleaned panel crops. Defaults are 512 pixels across each panel's artwork and 1024 across each summary panel's artwork. Final fitting uses nearest-neighbor resizing without dithering or gray fringes.

**Caption lines:** pick a .txt file on the main screen (remembered between launches). Each stylized carousel folder gets a `<carousel-folder-name>.txt` holding one randomly chosen non-blank line from it. Stylized folders are named `stylized-carousel-<date>_<time>-<NN>`; NN is the comic's position in the run. Leave the field blank to skip captions.

**Ad slide:** tick *Add random ad before summary* and choose a folder of images (png, jpg, tif, bmp, webp, gif). One random image per comic is saved as `image-N.png` right before the summary, and the summary shifts to the next number. It applies to the stylized carousel only. Non-PNG images are converted to PNG; PNGs are copied unchanged.

`all-social-carousels` holds a complete copy of every stylized folder (slides, ad, caption) so you can drag them all at once.

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

## Full export folders

```text
comic-maker-run-001/
  Comic A/
    print/
      full-page.png
      full-page.tif
      comic.png
      comic.tif
    regular-carousel/
      image-1.png
      image-2.png
      image-3.png
    stylized-carousel-2026-10-17_1032-01/
      image-1.png
      image-2.png
      image-3.png            (only with the ad toggle)
      image-4.png
      stylized-carousel-2026-10-17_1032-01.txt (only with a caption file)
    manifest.json
  Comic A-2/
    ...
  all-book-prints/
    Comic A--comic.png
    Comic A--comic.tif
    Comic A-2--comic.png
    Comic A-2--comic.tif
  all-processed-pages/
    Comic A--full-page.png
    Comic A--full-page.tif
    Comic A-2--full-page.png
    Comic A-2--full-page.tif
  all-full-size-pages/
    Comic A.tiff            (same format as its source scan)
    Comic A-2.jpg
  all-social/
    Comic A--stylized-carousel--image-1.png
    Comic A--stylized-carousel--image-2.png
    Comic A--stylized-carousel--image-3.png
    ...
  all-social-carousels/
    stylized-carousel-2026-10-17_1032-01/   (full copy of each comic's folder)
    stylized-carousel-2026-10-17_1032-02/
  batch-<id>.json
```

`all-full-size-pages` contains one cleaned, uncropped page per comic in its original image format and native pixel dimensions, regardless of print enlargement. Source DPI is retained where the format supports it (600 DPI if missing). JPEG sources use maximum-quality JPEG output. Existing runs fill this folder from their saved full-page print masters when the app opens, if the batch includes this collection.

`all-book-prints` collects the cleaned comic-only crops; `all-processed-pages` collects the cleaned full scans. Both contain byte-identical PNG and TIFF copies, retaining print dimensions and DPI. Completed print stages are collected even if a later carousel stage fails. Regeneration refreshes the affected copies; restarting or retrying also repairs missing copies. Opening the app fills in missing collections for existing version 2 runs from saved exports.

The summary is saved once in each version, numbered after its panels. `all-social` contains byte-identical copies of only the Red Sun stylized slides and summaries. Regular slides stay in each comic’s `regular-carousel` folder. Prefixes distinguish comics; numbers preserve reading order. Duplicate comic names receive deterministic suffixes. Marked export folders are excluded from recursive discovery.

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

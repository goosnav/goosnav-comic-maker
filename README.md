# Goosnav Comic Maker

Double-click **`run.command`** on macOS, or run:

```sh
./run.sh
```

The launcher opens the local browser app. First launch needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and internet access to install a private Python environment. Later local processing works offline. Windows: `./run.ps1` in PowerShell. Linux: `./run.sh`.

Choose one or more folders, choose a finish, choose an export folder, and press **Process comics**. Every comic gets its own folder with finished artwork, numbered carousel slides, and a final summary. Originals are never modified.

## The daily workflow

1. **Add originals.** Use **+ Folder**, **+ Files**, or paste paths. Subfolders are included by default. All supported images are included; use a filename filter such as `*raw*` if a folder mixes scans and finished art. The app skips its own export folders and the older tools’ numbered export folders.
2. **Choose the finish.** Clean ink preserves the original pixel grid. Paint adds a separate crunchiness control. Ink strength retains more or less faint pencil; dust removal removes tiny isolated marks. Make a preview, compare the original, and inspect native pixels when needed.
3. **Export.** The default canvas is 1080 × 1350. A 2160 × 2700 option is available. The summary rearranges panels to fit while preserving reading order. One source image is one comic.
4. **Return to the batch.** Refreshing or closing the browser tab does not interrupt processing. Relaunching after a crash resumes unfinished jobs. **Stop batch** stops between processing operations; **Retry unfinished** resumes failed or stopped items. Completed identical exports are reused after their file hashes are checked.

**Inspect / reprocess** brings a comic back to the workbench. **Edit panels** lets you draw, delete, and reorder boxes. A corrected crop or changed finish produces a different export folder. **Reprocess completed comics** explicitly creates a new version, which can make new paid AI requests.

## Quality controls

The local pipeline normalizes paper shading, separates pencil from paper, finds and straightens panels, and renders the final images. Black-and-white outputs contain only black and white, including resized carousel images. There is no dithering. Color outputs use a limited palette shared across the page and slides. Gray edge blends cannot borrow unrelated colored fills.

Crunchiness reduces the working pixel grid independently of threshold and final output size. Enlargement uses nearest-neighbor pixels. It makes an image larger without blur; it cannot recover details that were absent from the source. The normal fit-to-window preview uses browser display scaling for readability. **Inspect pixels** shows the exported grid without smoothing.

Already-colored artwork is detected automatically and cleaned locally, without an AI call. Override the input type in Settings when needed. The app always retains a black-and-white set.

## Optional colorization — beta

Enable **Also make a color version** and configure Settings once. The default prompt asks for color only, with exact lettering, linework and composition preserved. You can edit it, restore it, or leave it blank to use the built-in prompt.

- OpenAI direct image edits: default `gpt-image-2.5-sunburst`; configurable model, quality, and image size.
- Google Gemini direct image edits: `gemini-3-pro-image` or `gemini-3.1-flash-image`; configurable model and size.
- **FIXTURE**: deterministic test colors, no API calls. This is clearly labeled and is not AI output.

Provider request parameters follow the [OpenAI image guide](https://developers.openai.com/api/docs/guides/image-generation) and [Gemini image guide](https://ai.google.dev/gemini-api/docs/image-generation), checked September 21, 2026. Account model access can vary. Unsupported custom models report a per-comic failure while retaining local exports.

AI sees the cleaned comic, with unnecessary scanner margins removed. Results are flattened into discrete colors and resized without adding intermediate colors. The original linework is **not** multiplied over the generated drawing. Structural checks detect missing or displaced dark lines and large changes in dark coverage. A suspect result may receive one corrective retry, then is saved under `color-draft`. These checks cannot guarantee unchanged text, faces or meaning. Inspect beta output before publishing.

Vision recovery is optional and uses the configured OpenAI vision model after local panel-detection attempts remain uncertain. Local candidates remain available if the request fails. Best estimated crops are exported with a processing note. When there are no usable candidates, the full image is exported as one panel.

**Cost control:** default two attempts per AI stage, at most 30 requests per batch. Every HTTP attempt counts. A limit of zero disables paid requests. This is a request count, not a dollar budget. Rate/connection/server failures get bounded backoff; bad keys, rejected requests and exhausted account quota do not loop. Successful image responses are checkpointed for recovery. An interrupted request with no returned response can still have incurred a provider charge.

Keys can be entered in Settings or supplied as `OPENAI_API_KEY` and `GEMINI_API_KEY` to the launcher process. Saved keys take precedence and are stored in an owner-only file outside the project. Settings show only whether a key exists. Images leave the computer only when AI features are enabled; color goes to the selected provider, vision recovery goes to OpenAI.

## Export contents

```text
exports/
  comic-name--recipe-id/
    bw/
      comic.png           # cropped, finished comic at source resolution
      source-layout.png   # complete processed scan
      01-panel.png
      02-panel.png
      03-summary.png      # final slide in upload order
      summary.png         # convenient duplicate of the final slide
    color/                # optional; color-draft/ when flagged
      comic.png
      source-layout.png
      01-panel.png
      02-panel.png
      03-summary.png
      summary.png
      provider-output.png # original provider response, when AI was used
    manifest.json         # settings, source hash, crops, checks, output hashes
  batch-id.json           # readable batch report
```

Upload the numbered slides, in order. `summary.png` duplicates the last numbered slide; do not upload both. Filenames include a recipe identifier so duplicate names and alternate finishes cannot overwrite each other. Original scans, exports and secrets are excluded from source control.

Settings, preview caches and SQLite job history live in:

- macOS: `~/Library/Application Support/GoosnavComicMaker`
- Windows: `%LOCALAPPDATA%/GoosnavComicMaker`
- Linux: `$XDG_DATA_HOME/GoosnavComicMaker` or `~/.local/share/GoosnavComicMaker`

Override with `COMIC_MAKER_DATA` for isolated testing. Keep exports separate from the folder containing originals. The local server binds only to loopback, checks Host and Origin, and requires a session token for mutations.

## Batch CLI

```sh
./run.sh batch "/path/to/scans" "/another/folder" --out "/path/to/exports" --json
./run.sh batch "/path/to/scans" --out "/path/to/exports" --style paint --pattern '*raw*'
./run.sh batch "/path/to/scans" --out "/path/to/exports" --colorize
./run.sh batch "/path/to/test-images" --out "/path/to/test-exports" --fixture
```

GUI and CLI use the same application service and saved settings. `--settings settings.json` supplies a full or partial settings object with default values for missing fields. Exit code 0 means all selected images completed; 2 means missing inputs or partial/failed results. CLI output manifests also permit completed work and saved provider responses to be reused on rerun. The persistent automatic queue belongs to the GUI.

## Verification and boundaries

```sh
./verify.sh
```

Runs image invariants, real scan panel regressions when private examples are present, recovery tests, provider mocks, a real loopback HTTP workflow, an offline color fixture, CLI checks, and clean shutdown. It does not spend API credits. See `VERIFICATION.md` for observed browser and live-provider checks.

This version targets bordered, mostly rectangular comics. It does not auto-publish, create captions, combine multiple scans into one comic, or accept PDF/PSD/multi-page TIFF. A single image is limited to 85 megapixels; requests for oversized master enlargements report an error. Unreadable files do not stop the batch. Best-guess crops can still need correction. macOS has been tested locally; Windows and Linux launcher scripts have not been run on their native platforms.

The useful panel-detection and summary-layout code was adapted from the existing Comic Carousel Generator. Scan and color processing were rebuilt here, informed by Red Sun and Auto Colorizer. The sibling apps are not runtime dependencies.

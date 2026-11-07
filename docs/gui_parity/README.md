# GUI parity: B210 GUI vs console and radar_gui

Shots: `before/` and `after/`, per GUI, 1280x900 and 390x844, captured with console's
`tools/pagecap.py --url` (navigate and screenshot only; B210 GUI on `--source none`, no radio).
Before: `before/b210/data-*` (the Configs tab could not be reached by link, so this is the File
view that always came up); radar_gui `before/radar_gui/{cfg,configs}-*`; console
`before/console/{record,settings_robots}-*` (`?demo=1`). After: `after/b210/{configs,data}-*`.

| # | Area | Gap | Sev | Status |
|---|------|-----|-----|--------|
| 1 | Layout/nav | Tabs not deep-linkable: `/#configs` ignored, so shots and bookmarks always landed on File view | high | fixed (hash routing) |
| 2 | Layout/nav | Flat page, no cards; others use bordered rounded panels | med | fixed (`.card`) |
| 3 | Layout/nav | Plain buttons for tabs; others use a segmented control with filled active tab | med | fixed (`.seg`) |
| 4 | Colours/type | Different palette; title-case headings vs muted uppercase card headings | med | fixed (radar_gui palette) |
| 5 | Controls | Primary action not distinguished; no hover/focus ring | med | fixed (`.primary`, hover, focus-visible) |
| 6 | Status/error | Connection state as bare text; others use a pill with coloured dot | med | fixed (`#statuspill`) |
| 7 | Status/error | Config issues as bare coloured lines | low | fixed (severity-bordered rows) |
| 8 | Data view | Nothing open showed two empty black canvases and dead transport controls | high | fixed (empty-state text; canvases and transport appear on open) |
| 9 | Data view | Mobile: slider fixed 16em | low | fixed (full width under 600px) |
| 10 | Config edit | Edit shipped configs in place (radar_gui, with confirm) | - | defer: shipped configs are read-only by design (sha256 test); copy-to-new stays the workflow |
| 11 | Header | Live stats strip and session pill with Set up | - | defer: needs capture/session; out of scope (no radio) |
| 12 | Data view | 3D/point cloud, ADC, Tune, Logs tabs | - | defer: intentionally dropped (no point cloud, no DCA1000) |
| 13 | Config list | Filter box, multi-select delete, file-preview pane | low | defer: list is tiny; revisit when it grows |

# Local XPS/XRD integration

This local extension adapts the data recognition, validation, grouping and native
graph layout from the user's `g457337844-spec/plotter` repository at
`7f32b1acfe17450b3a582a1d38b05118e7679a28`. It uses the existing authenticated Origin
bridge and does not launch the plotter desktop application or another COM session.
The source has been adapted for the user's local integration; it has not been
published to either upstream repository.

## The three tools

- `origin_plan_spectra`: read and check files, show column roles, warnings, resolved
  colors, and the graph plan. This requires no running Origin and changes no project.
- `origin_plot_spectra`: repeat validation, optionally check a prior `plan_id`, and
  create native workbooks and graphs in the current Origin project. It never clears,
  opens or saves a project. Optional PNG/PDF/SVG/TIF exports do not overwrite files.
- `origin_spectra_presets`: list palettes/defaults or save an appearance-only JSON
  file. Use its path as `style_path` on subsequent plan/plot calls.

These tools are included in compact and standard profiles. All existing tools remain.

## Normal use

The same request is accepted by plan and plot:

```json
{
  "sources": [{"path": "C:/data/sample.xlsx", "sheets": ["O1s"]}],
  "options": {"style": {"palette": "muted"}}
}
```

Only `sources` is required. XPS uses a decreasing binding-energy axis by default;
XRD retains its original intensity unless `normalize_xrd` is explicitly enabled.
`mode` accepts `separate`, `stack`, or `overlay`. XPS regions are grouped separately;
multi-sample XPS overlays show the measured curves, retaining other columns in the
workbooks. XRD reference-card text is optional and adds separate peak-stick layers.

XPS supports XLSX/XLSM/XLS and delimited text with recognized energy headers. XLS
uses the already-declared xlrd dependency and does not start Excel. XRD supports
TXT/CSV/TSV/DAT/XY/RAS numeric text; this is not a universal binary RAS decoder.
PDF# means an exported text peak table, not a binary PDF document or a database lookup.

Sources can specify `kind`, `sheets`, `label`, `region`, `color`, and a `columns`
object. Source column indexes are **one-based**. For an ambiguous XPS table:

```json
{"path":"C:/data/sample.xlsx", "columns":{
  "x_column":1, "y_column":3, "background_column":4,
  "envelope_column":5, "peak_columns":[6,7,8]
}}
```

For two XRD scans in one file, give two source entries with different `y_column`
values and labels. Extra numeric columns require an explicit mapping, so scans
cannot be silently discarded. Any invalid selected source prevents the whole batch
from drawing. Unselected Excel sheets can be excluded with `sheets`.

## Appearance without hidden data changes

Built-in palettes: `plotter` (the original software's colors), `muted`, `contrast`.
The default is `plotter`. `options.style` supports custom `colors`, `peak_colors`,
role colors, line width, peak fill, legend, tick values, font, size, and an Origin
template. `fill_strength` is a white tint mix (0=white, 1=full component color).
It is not native alpha transparency. Omitted font and size preserve the template.
Templates must contain a single empty layer; native page geometry is retained.

Precedence is defaults → saved style file → explicit style fields → source color.
Changing a palette does not change normalization, the selected data or the layout.
Presets contain visual fields only. For example:

```json
{"action":"save", "path":"C:/data/my-style.json",
 "style":{"palette":"muted", "legend":true, "line_width":1.5}}
```

Raw data remain in their source files and native worksheets. XRD min–max
normalization creates a separate display column using the full input spectrum:
`(I - min(I)) / (max(I) - min(I)) * 100`. A constant scan produces zeros.

For XPS, inputs are already-fitted components, not fitting instructions.
`peak_baseline="background"` fills each supplied component to the background
column. If the background is missing, filling is skipped with a warning. For
baseline-subtracted components, explicitly use `peak_baseline="zero"`.
The workflow never adds backgrounds to component values, computes a fit, assigns
chemical states or identifies XRD phases. Those remain separate analysis tasks.

## Reproducibility and failure handling

The plan includes file SHA-256 hashes, effective options, source mappings and a
`plan_id`. Passing it as `expected_plan_id` rejects changed files/settings before
Origin is modified. Data are read back from every new worksheet and checked for
point count, missing values and numeric agreement. The bridge executes each batch
under its existing Origin call lock on the UI thread. A rendering failure cleans
up only pages created by that transaction. Export failures preserve the created
graphs and return their identities and individual errors.

The embedded renderer uses standard-library option validation and the bridge's
existing pandas/originpro dependencies. Pydantic is used only by the MCP server;
Origin's internal Python does not need it installed. Tests enforce that boundary.

The renderer completes Origin's deferred graph updates before placing native axis
titles, then verifies their positions after repainting. Decreasing XPS scales use
descending From/To limits, so the intensity axis stays on the left. Stack panels
inherit tick fonts and share a single X axis without repeated panel borders.

## Local visual compatibility finding

The 2026-10-05 smoke tests on this machine's OriginPro 2024 instance produce an
unwanted overline above plain axis-title and legend text. Text contents are plain
strings and resetting their text/font does not remove it. Data, plot colors,
editable objects, axis placement and exports were verified; this text-decoration
issue remains unresolved. Similar behavior in Chinese Origin was reported to
[OriginLab support](https://my.originlab.com/forum/topic.asp?TOPIC_ID=48367).
No language preferences, Origin installation files or user projects were changed
to work around it. Review the exported samples before using them in a manuscript.

After source changes affecting the renderer, follow `AGENTS.md`: stop the bridge,
rebuild/install the Start/Stop Apps, restart the installed Start App, then run
`origin-mcp status` and `origin-mcp doctor --ping-origin`. Restart/reconnect the MCP
client when it retains an older tools list. No changes to GitHub or plugin accounts
are required for this local integration.

Origin API references used during integration:

- https://docs.originlab.com/labtalk/ref/window-cmd/
- https://docs.originlab.com/labtalk/ref/layer-axis-obj/

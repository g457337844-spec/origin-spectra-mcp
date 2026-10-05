"""Native Origin rendering adapted from plotter 7f32b1acfe17.

Runs only inside the existing bridge's Origin/UI thread; opens no COM session.
"""

from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING, Any

import pandas as pd

from origin_mcp.errors import OriginOperationError

from .models import Curve, Dataset
from .planning import GraphPlan, axis_titles, display_values, validate_selection

if TYPE_CHECKING:
    from .config import SpectraOptions


def quote(value):
    return (
        str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    )


def formula_markup(value):
    return quote(value)


def fill_color(value: str, opacity: float) -> str:
    rgb = [int(value[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(v * opacity + 255 * (1 - opacity)):02X}" for v in rgb)


GEOMETRY = (
    "page.width",
    "page.height",
    "page.resx",
    "page.resy",
    "layer.unit",
    "layer.left",
    "layer.top",
    "layer.width",
    "layer.height",
)


def execute(client, script, context=None):
    if context:
        script = f"win -a {context};\n" + script
    response = client.run_labtalk(script)
    if response.get("result") is False:
        raise OriginOperationError(
            "Origin 拒绝了光谱绘图指令：" + script[:240], error_code="spectra_render_failed"
        )


def settle_graph(client, graph):
    """Complete deferred native scale/label updates before positioning objects."""
    execute(client, "doc -uw;", graph)
    client.op.wait("s", 0.1)


def axis_limits(plan, options):
    if plan.x_range is None:
        raise ValueError("Plot range has not been validated")
    low, high = plan.x_range
    return (high, low) if options.reverse_xps and plan.kind == "xps" else (low, high)


def snapshot(client):
    result = {key: float(client.op.lt_float(key)) for key in GEOMETRY}
    x0, x1, y0, y1 = [
        float(client.op.lt_float("layer." + s)) for s in ("x.from", "x.to", "y.from", "y.to")
    ]
    for obj in ("xb", "yl"):
        result[obj] = dict(
            text=str(client.op.get_lt_str(f"{obj}.text$")),
            size=float(client.op.lt_float(f"{obj}.fsize")),
            attach=float(client.op.lt_float(f"{obj}.attach")),
            show=float(client.op.lt_float(f"{obj}.show")),
            font=float(client.op.lt_float(f"{obj}.font")),
            normalized_x=(float(client.op.lt_float(f"{obj}.x")) - x0) / (x1 - x0),
            normalized_y=(float(client.op.lt_float(f"{obj}.y")) - y0) / (y1 - y0),
        )
    result["ticks"] = {a: float(client.op.lt_float(f"layer.{a}.label.pt")) for a in ("x", "y")}
    result["tick_fonts"] = {
        a: float(client.op.lt_float(f"layer.{a}.label.font")) for a in ("x", "y")
    }
    result["element_scale"] = float(client.op.lt_float("layer.factor"))
    result["fixed_scaling"] = float(client.op.lt_float("layer.fixed"))
    result["y_label_format"] = int(client.op.lt_float("layer.y.label.numFormat"))
    result["y_decimals"] = int(client.op.lt_float("layer.y.decPlaces"))
    result["legend"] = str(client.op.get_lt_str("legend.text$"))
    result["x_range"] = [x0, x1]
    result["y_range"] = [y0, y1]
    return result


def tick_width(client, graph, texts, size):
    """Measure with Origin's own font renderer; remove the temporary ruler."""
    widths = []
    for value in texts:
        execute(
            client,
            f'label -n GSTickMeasure "{quote(value)}"; '
            f"GSTickMeasure.font=layer.y.label.font; "
            f"GSTickMeasure.fsize={size:g}; "
            f"GSTickMeasure.show=0;",
            graph,
        )
        widths.append(
            abs(float(client.op.lt_float("GSTickMeasure.dx")))
            / abs(
                float(client.op.lt_float("layer.x.to")) - float(client.op.lt_float("layer.x.from"))
            )
        )
    execute(client, "label -r GSTickMeasure;", graph)
    return max(widths, default=0)


def tick_texts(audit):
    start, end = audit["y_range"]
    decimals = max(0, min(audit["y_decimals"], 8))
    values = [start, end, 0, (start + end) / 2]
    mode = audit["y_label_format"]
    if mode == 2:
        return [f"{v:.{decimals}e}" for v in values]
    if mode == 4:
        return [f"{v:,.{decimals}f}" for v in values]
    return [f"{v:.{decimals}f}" for v in values]


def make_workbook(client, ds: Dataset, options: SpectraOptions, created: list):
    wks = client.op.new_sheet("w")
    book = wks.get_book()
    created.append(book)
    book.lname = f"{ds.label} · {ds.sheet or ds.kind.upper()}"
    curves = list(ds.curves)
    if ds.kind == "xrd" and options.normalize_xrd:
        curves.append(Curve("display", "Normalized intensity", display_values(ds, options), 0, "%"))
    if ds.kind == "xps" and options.peak_baseline == "zero":
        curves.append(
            Curve("fill_baseline", "Zero fill baseline", [0.0] * len(ds.x), 0, ds.measured.unit)
        )
    if ds.kind == "reference":
        rows = []
        for x, y in zip(ds.x, curves[0].values, strict=True):
            rows.extend([[x, 0.0], [x, y], [None, None]])
    else:
        rows = [[x] + [c.values[i] for c in curves] for i, x in enumerate(ds.x)]
    frame = pd.DataFrame(rows, columns=["X"] + [f"Y{i + 1}" for i in range(len(curves))])
    wks.from_df(frame)
    client._trim_worksheet_columns(wks, len(frame.columns))
    wks.cols_axis("xy")
    wks.set_labels(
        ["Binding Energy" if ds.kind == "xps" else "2 Theta"] + [c.label for c in curves], "L"
    )
    wks.set_labels([ds.x_unit] + [c.unit for c in curves], "U")
    wks.set_labels(
        [ds.source]
        + [
            f"{ds.source}; sheet={ds.sheet}; column={c.source_column}; role={c.role}"
            for c in curves
        ],
        "C",
    )
    received = wks.to_df().iloc[: len(rows), : len(frame.columns)]
    if received.shape != frame.shape:
        raise OriginOperationError("Origin 回读的数据行列数量不一致")
    max_error = 0.0
    for want, got in zip(frame.to_numpy().flat, received.to_numpy().flat, strict=True):
        if pd.isna(want):
            if not pd.isna(got):
                raise OriginOperationError("Origin 未保留缺失点")
        else:
            if pd.isna(got) or not math.isfinite(float(got)):
                raise OriginOperationError("Origin 回读数据缺失")
            delta = abs(float(want) - float(got))
            max_error = max(max_error, delta)
            if delta > max(abs(float(want)) * 1e-10, 1e-8):
                raise OriginOperationError("Origin 回读数据与原始数据不一致")
    return dict(
        page=book.name,
        sheet=wks.name,
        curves=curves,
        roundtrip_max_error=max_error,
        rows=len(rows),
        columns=len(frame.columns),
    )


def add_curve(client, graph, layer, book, column, hex_color, plot=200):
    execute(
        client,
        f"plotxy iy:=[{book['page']}]{book['sheet']}!(1,{column}) plot:={plot} "
        f'color:=color("{hex_color}") legend:=0 ogl:=[{graph}]{layer}!;',
    )
    target = client.op.find_graph(graph)[layer - 1]
    return len(target.plot_list())


def plot_style(client, graph, layer, index, hex_color, width=1.5, hidden=False, symbol=False):
    cmds = [
        f"layer -s {layer};",
        f'layer.plot{index}.color=color("{hex_color}");',
        f"range __gsplot=!{index}; set __gsplot -lm 0;",
    ]
    if symbol:
        cmds.extend([f"layer.plot{index}.symbol.size=3;", f"layer.plot{index}.symbol.kind=2;"])
    else:
        cmds.extend(
            [
                f"layer.plot{index}.line.width={width:g};",
                f"layer.plot{index}.line.type={0 if hidden else 1};",
            ]
        )
    execute(client, "\n".join(cmds), graph)


def draw_dataset(client, graph, layer, ds, book, options, comparison=False):
    curves = book["curves"]
    report = []
    if ds.kind == "reference":
        i = add_curve(client, graph, layer, book, 2, ds.color, 200)
        plot_style(client, graph, layer, i, ds.color, options.style.line_width)
        return [dict(role="sticks", index=i, color=ds.color)]
    if ds.kind == "xrd" or comparison:
        col = (
            len(curves) + 1
            if ds.kind == "xrd" and options.normalize_xrd
            else next(i + 2 for i, c in enumerate(curves) if c.role == "measured")
        )
        i = add_curve(client, graph, layer, book, col, ds.color)
        plot_style(client, graph, layer, i, ds.color, options.style.line_width)
        return [dict(role="measured", index=i, color=ds.color)]
    peaks = [(i + 2, c) for i, c in enumerate(curves) if c.role == "peak"]
    peaks.sort(
        key=lambda pair: ds.x[
            max(
                range(len(ds.x)),
                key=lambda k: pair[1].values[k] if pair[1].values[k] is not None else -math.inf,
            )
        ],
        reverse=True,
    )
    baseline_role = "fill_baseline" if options.peak_baseline == "zero" else "background"
    background = next((i + 2 for i, c in enumerate(curves) if c.role == baseline_role), None)
    for k, (col, c) in enumerate(peaks):
        colors = options.style.component_colors()
        hex_color = colors[k % len(colors)]
        i = add_curve(client, graph, layer, book, col, hex_color)
        plot_style(client, graph, layer, i, hex_color, options.style.line_width)
        if options.style.peak_fill and background:
            boundary = add_curve(client, graph, layer, book, background, hex_color)
            plot_style(client, graph, layer, boundary, hex_color, 0, hidden=True)
            execute(
                client,
                f"layer -s {layer}; "
                f"range __gsfill=!{i}; "
                f"set __gsfill -pf 1; "
                f"set __gsfill -pfv 8; "
                f"set __gsfill -pfn 1; "
                f'set __gsfill -pfb color("{fill_color(hex_color, options.style.fill_strength)}"); '
                f"set __gsfill -pfm 3; "
                f'set __gsfill -pff color("#FFFFFF");',
                graph,
            )
        report.append(dict(role="peak", index=i, color=hex_color, column=col, label=c.label))
    for role, hex_color in (
        ("background", options.style.background_color),
        ("envelope", options.style.envelope_color),
        (
            "measured",
            ds.color
            if ds.metadata.get("explicit_color")
            else options.style.measured_color or ds.color,
        ),
    ):
        for col, c in enumerate(curves, 2):
            if c.role != role:
                continue
            symbol = role == "measured" and bool(peaks)
            i = add_curve(client, graph, layer, book, col, hex_color, 201 if symbol else 200)
            plot_style(client, graph, layer, i, hex_color, options.style.line_width, symbol=symbol)
            report.append(dict(role=role, index=i, color=hex_color, column=col, label=c.label))
    return report


def arrange(client, graph, panels, baseline):
    if len(panels) == 1:
        return
    # Layer 1 keeps the default frame and owns the shared native axis titles.
    # Child plot layers only divide the original rectangle vertically.
    for _ in range(len(panels)):
        execute(client, f"layadd igp:={graph} type:=normal activate:=1;")
    weights = [0.4 if p[0].kind == "reference" else 1 for p in panels]
    top = baseline["layer.top"]
    height = baseline["layer.height"]
    total = sum(weights)
    # A real gap keeps default-size tick labels from touching the next panel.
    # The first and last frame edges still match the native template exactly.
    gap = height * 0.045
    available = height - gap * (len(panels) - 1)
    for i, w in enumerate(weights, 2):
        h = available * w / total
        execute(
            client,
            f"layer -s {i}; "
            f"layer.fixed=1; "
            f"layer.factor={baseline['element_scale']:g}; "
            f"layer.unit={baseline['layer.unit']:g}; "
            f"layer.left={baseline['layer.left']:g}; "
            f"layer.width={baseline['layer.width']:g}; "
            f"layer.top={top:g}; "
            f"layer.height={h:g}; "
            f"layer.x.label.pt={baseline['ticks']['x']:g}; "
            f"layer.y.label.pt={baseline['ticks']['y']:g}; "
            f"layer.x.label.font={baseline['tick_fonts']['x']:g}; "
            f"layer.y.label.font={baseline['tick_fonts']['y']:g};",
            graph,
        )
        top += h + gap


def render_graph(client, plan: GraphPlan, options: SpectraOptions, books, created, preview_dir):
    if plan.x_range is None:
        raise ValueError("Plot range has not been validated")
    graph = client.op.new_graph(template=options.style.template)
    if graph is None:
        raise OriginOperationError(f"无法使用模板 {options.style.template} 创建图页")
    created.append(graph)
    if len(graph) != 1:
        raise OriginOperationError("光谱模板必须恰好包含一个空图层")
    if graph[0].plot_list():
        raise OriginOperationError("光谱模板必须是无曲线的空模板")
    graph.name = "SP" + uuid.uuid4().hex[:9]
    name = graph.name
    graph.lname = plan.title
    execute(client, f"win -a {name}; layer -s 1;")
    if options.style.font:
        font = options.style.font
        execute(
            client,
            f'layer.x.label.font=font("{font}"); '
            f'layer.y.label.font=font("{font}"); '
            f'xb.font=font("{font}"); '
            f'yl.font=font("{font}");',
        )
    if options.style.font_size:
        size = options.style.font_size
        execute(
            client,
            f"layer.x.label.pt={size:g}; "
            f"layer.y.label.pt={size:g}; "
            f"xb.fsize={size:g}; "
            f"yl.fsize={size:g};",
        )
    settle_graph(client, name)
    baseline = snapshot(client)
    baseline_tick_width = tick_width(client, name, tick_texts(baseline), baseline["ticks"]["y"])
    arrange(client, name, plan.panels, baseline)
    all_curves = []
    audits = []
    legends = []
    multi = len(plan.panels) > 1
    if multi:
        first = plan.panels[0][0]
        firstbook = books[first.id]
        col = (
            len(firstbook["curves"]) + 1
            if first.kind == "xrd" and options.normalize_xrd
            else next(j + 2 for j, c in enumerate(firstbook["curves"]) if c.role == "measured")
        )
        add_curve(client, name, 1, firstbook, col, first.color)
        x_title, y_title = axis_titles(first, options)
        start, end = axis_limits(plan, options)
        execute(
            client,
            f"layer -s 1; "
            f"rescale; "
            f"layer.showData=0; "
            f"layer.x.reverse=0; "
            f"layer.x.from={start:.15g}; "
            f"layer.x.to={end:.15g}; "
            f"layer.x.rescale=1; "
            f"layer.y.rescale=1; "
            f"layer.y.showLabels=0; "
            f"layer.y.showAxes=0; "
            f"layer.y.ticks=0; "
            f'xb.text$="{quote(x_title)}"; '
            f'yl.text$="{quote(y_title)}"; '
            f"label -r legend;",
            name,
        )
        audits.append(snapshot(client))
    for panel_index, panel in enumerate(plan.panels, 1):
        i = panel_index + 1 if multi else panel_index
        comparison = len(panel) > 1
        reports = []
        for ds in panel:
            reports.extend(draw_dataset(client, name, i, ds, books[ds.id], options, comparison))
        execute(client, f"layer -s {i}; rescale;", name)
        low, high = plan.x_range
        start, end = axis_limits(plan, options)
        x_title, y_title = axis_titles(panel[0], options)
        cmds = [
            f"layer -s {i};",
            "layer.x.reverse=0;",
            f"layer.x.from={start:.15g};",
            f"layer.x.to={end:.15g};",
            "layer.x.rescale=1;",
            "layer.y.rescale=1;",
            f'xb.text$="{quote(x_title)}";',
            f'yl.text$="{quote(y_title)}";',
            f"layer.y.showLabels={int(options.style.show_y_values)};",
            "layer.y.postype=0;",
            "layer.y.majorTicks=5;",
        ]
        # Native titles keep their template anchor, font and automatic positioning.
        if multi:
            cmds.extend(
                [
                    "xb.show=0;",
                    "layer.x.showLabels=0;",
                    "layer.x.showAxes=0;",
                    f"layer.y.showAxes={int(options.style.show_y_values)};",
                ]
            )
        else:
            cmds.append("xb.show=1;")
        cmds.append("yl.show=0;" if multi or panel[0].kind == "reference" else "yl.show=1;")
        if panel[0].kind == "reference":
            top = max(v for v in panel[0].curves[0].values if v is not None)
            cmds.extend(
                [
                    "layer.y.from=0;",
                    f"layer.y.to={max(top * 1.1, 1):.15g};",
                    "layer.y.showLabels=0;",
                    "layer.y.showAxes=0;",
                ]
            )
        if options.style.legend or comparison or len(plan.panels) > 1:
            if comparison:
                legend = "\\n".join(
                    f"\\l({reports[k]['index']}) {formula_markup(d.label)}"
                    for k, d in enumerate(panel)
                )
            elif len(plan.panels) > 1 or panel[0].kind == "reference":
                measured = next(
                    (p["index"] for p in reports if p["role"] in {"measured", "sticks"}),
                    reports[-1]["index"],
                )
                legend = f"\\l({measured}) {formula_markup(panel[0].label)}"
            else:
                names = {
                    "background": "Background",
                    "envelope": "Fit envelope",
                    "measured": "Measured",
                }
                peaknum = 0
                entries = []
                for plot in reports:
                    if plot["role"] == "peak":
                        peaknum += 1
                        title = plot["label"]
                    else:
                        title = names.get(plot["role"], plot["role"])
                    entries.append(f"\\l({plot['index']}) {quote(title)}")
                legend = "\\n".join(entries)
            legends.append((i, legend, len(plan.panels) > 1 or comparison))
        else:
            cmds.append("label -r legend;")
        execute(client, "\n".join(cmds), name)
        settle_graph(client, name)
        if options.peak_labels and len(panel) == 1 and panel[0].kind == "xps":
            ds = panel[0]
            y0 = float(client.op.lt_float("layer.y.from"))
            y1 = float(client.op.lt_float("layer.y.to"))
            for k, c in enumerate([c for c in ds.curves if c.role == "peak"]):
                valid = [
                    j
                    for j, (x, y) in enumerate(zip(ds.x, c.values, strict=True))
                    if low <= x <= high and y is not None
                ]
                if not valid:
                    continue
                j = max(valid, key=lambda j: float(c.values[j] or 0.0))
                x = ds.x[j]
                y = c.values[j]
                assert y is not None
                obj = f"GSPeak{k + 1}"
                execute(
                    client,
                    f'label -n {obj} "{x:.1f} eV"; '
                    f"{obj}.attach=2; "
                    f"{obj}.x={x:.15g}; "
                    f"{obj}.y={min(y + (y1 - y0) * 0.05, y1 - (y1 - y0) * 0.04):.15g}; "
                    f"{obj}.fsize={baseline['xb']['size'] * 0.6:g};",
                    name,
                )
        audits.append(snapshot(client))
        all_curves.append(reports)
    if multi:
        execute(
            client,
            f'laylink igp:={name} igl:=1 destlayers:="2:{len(plan.panels) + 1}" '
            "XAxis:=1 YAxis:=0 unit:=page;",
            name,
        )
    settle_graph(client, name)
    for i, legend, inside in legends:
        cmds = [
            f"layer -s {i};",
            "label -r legend;",
            f'label -sa -n legend "{legend}";',
            "legend.show=1;",
            "legend.attach=2;",
            "legend.background=0;",
            f"legend.font={baseline['xb']['font']:g};",
            f"legend.fsize={baseline['xb']['size'] * 0.65:g};",
        ]
        execute(client, "\n".join(cmds), name)
        settle_graph(client, name)
        # Signed widths follow descending XPS scales without moving the Y axis.
        x_position = (
            "layer.x.to-legend.dx/2-(layer.x.to-layer.x.from)*0.025"
            if inside
            else "layer.x.from+legend.dx/2+(layer.x.to-layer.x.from)*0.025"
        )
        execute(
            client,
            f"legend.x={x_position}; "
            "legend.y=layer.y.to-abs(legend.dy)/2-(layer.y.to-layer.y.from)*0.045;",
            name,
        )
    if multi:
        widths = []
        for i in range(2, len(plan.panels) + 2):
            execute(client, f"layer -s {i};", name)
            if plan.panels[i - 2][0].kind != "reference":
                widths.append(
                    tick_width(client, name, tick_texts(snapshot(client)), baseline["ticks"]["y"])
                )
    else:
        widths = [tick_width(client, name, tick_texts(snapshot(client)), baseline["ticks"]["y"])]
    offset = baseline["yl"]["normalized_x"]
    if options.style.show_y_values:
        offset -= max(0, max(widths, default=0) - baseline_tick_width)
    # Rescaling several curves can leave titles at an earlier scale coordinate.
    # Preserve the template's relative center without changing its fonts/anchors.
    execute(
        client,
        f"layer -s 1; "
        f"xb.x=layer.x.from+(layer.x.to-layer.x.from)*{baseline['xb']['normalized_x']:.15g}; "
        f"xb.y=layer.y.from+(layer.y.to-layer.y.from)*{baseline['xb']['normalized_y']:.15g}; "
        f"yl.x=layer.x.from+(layer.x.to-layer.x.from)*{offset:.15g}; "
        f"yl.y=layer.y.from+(layer.y.to-layer.y.from)*{baseline['yl']['normalized_y']:.15g};",
        name,
    )
    settle_graph(client, name)
    audits = []
    for i in range(1, len(plan.panels) + 1 + int(multi)):
        execute(client, f"layer -s {i};", name)
        audits.append(snapshot(client))
    # Geometry changes are treated as errors, never silently fixed by enlarging the page.
    for audit in audits:
        for key in (
            "page.width",
            "page.height",
            "page.resx",
            "page.resy",
            "layer.left",
            "layer.width",
        ):
            if not math.isclose(audit[key], baseline[key], abs_tol=1e-6):
                raise RuntimeError(f"Origin 改变了默认尺寸：{key}")
    if multi:
        # Origin rounds layer rectangles to native page pixels.
        tolerance = (100 / baseline["page.height"] + 1e-6) if baseline["layer.unit"] == 1 else 0.003
        if (
            abs(audits[1]["layer.top"] - baseline["layer.top"]) > tolerance
            or abs(
                audits[-1]["layer.top"]
                + audits[-1]["layer.height"]
                - baseline["layer.top"]
                - baseline["layer.height"]
            )
            > tolerance
        ):
            raise RuntimeError("多层图超出了默认绘图区范围")
    if len(plan.panels) == 1:
        for key in ("layer.top", "layer.height"):
            if not math.isclose(audits[0][key], baseline[key], abs_tol=1e-6):
                raise RuntimeError("Origin 改变了默认绘图区位置")
    for obj in ("xb", "yl"):
        for key in ("size", "attach", "font"):
            if not math.isclose(audits[0][obj][key], baseline[obj][key], abs_tol=0.003):
                raise RuntimeError(f"原生轴标题的默认字体或关联发生变化：{obj}.{key}")
        for key, expected in (
            ("normalized_x", offset if obj == "yl" else baseline[obj]["normalized_x"]),
            ("normalized_y", baseline[obj]["normalized_y"]),
        ):
            if not math.isclose(audits[0][obj][key], expected, abs_tol=0.003):
                raise RuntimeError(f"Origin 刷新后轴标题的位置发生漂移：{obj}.{key}")
    preview = None
    return dict(
        graph=name,
        title=plan.title,
        kind=plan.kind,
        preview=preview,
        baseline=baseline,
        audit=audits,
        curves=all_curves,
        size_inches=[
            baseline["page.width"] / baseline["page.resx"],
            baseline["page.height"] / baseline["page.resy"],
        ],
        data=[d.id for p in plan.panels for d in p],
        data_layers=len(plan.panels),
        shared_native_axis_layer=multi,
        native_axis_titles=True,
        default_geometry_preserved=True,
    )


def render_spectra(client, datasets: list[Dataset], options: SpectraOptions):
    # Validate the bridge payload as well as the server-side files before any mutation.
    plans = validate_selection(datasets, options)
    created: list[Any] = []
    books = {}
    results = []
    try:
        for dataset in datasets:
            books[dataset.id] = make_workbook(client, dataset, options, created)
        for plan in plans:
            results.append(render_graph(client, plan, options, books, created, None))
        return {
            "graphs": results,
            "workbooks": [
                {
                    "dataset": key,
                    "page": value["page"],
                    "rows": value["rows"],
                    "columns": value["columns"],
                    "roundtrip_max_error": value["roundtrip_max_error"],
                }
                for key, value in books.items()
            ],
            "native_editable": True,
        }
    except Exception as exc:
        cleanup_errors = []
        for page in reversed(created):
            try:
                page.destroy()
            except Exception as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
        detail = "; 清理未完成: " + "; ".join(cleanup_errors) if cleanup_errors else ""
        raise OriginOperationError(
            f"光谱绘图失败：{exc}{detail}", error_code="spectra_render_failed"
        ) from exc

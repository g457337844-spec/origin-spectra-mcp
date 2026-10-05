"""Two-stage XPS/XRD workflow on the existing Origin bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from origin_mcp.errors import OriginOperationError
from origin_mcp.file_io import check_path_allowed
from origin_mcp.models import ToolResult
from origin_mcp.spectra.config import SpectraOptions, SpectraStyle, SpectrumSource
from origin_mcp.spectra.workflow import prepare, resolve_options, save_style, style_catalog

from ._shared import _mcp_tool, _ok, _wrap, client


def _invalid_plan(report):
    return ToolResult(
        ok=False,
        message="数据检查未通过，尚未修改 Origin。",
        error_code="invalid_request",
        data=report,
    ).model_dump(exclude_none=True)


@_mcp_tool(
    parameter_descriptions={
        "sources": "Files with optional kind, sheets, one-based column mapping, label and color.",
        "style_path": "Saved visual preset JSON; explicit style fields override it.",
    }
)
def origin_plan_spectra(
    sources: list[SpectrumSource],
    options: SpectraOptions | None = None,
    style_path: str | None = None,
) -> dict[str, Any]:
    """Inspect XPS/XRD files, column roles, warnings, colors and planned graphs, without Origin.

    Minimal input: sources=[{"path":"C:/data/O1s.xlsx"}]. Text files auto-detect
    XPS headers, XRD scans or PDF# reference-card text. Specify one-based columns
    when ambiguous. Appearance is options.style; normalization is opt-in.
    Pass the returned plan_id as expected_plan_id to origin_plot_spectra.
    """

    def run():
        _, report = prepare(sources, resolve_options(options, style_path))
        return (
            _ok("已检查光谱数据和绘图设置。", **report)
            if report["valid"]
            else _invalid_plan(report)
        )

    return _wrap(run)


@_mcp_tool(
    parameter_descriptions={
        "sources": "Files with optional kind, sheets, one-based column mapping, label and color.",
        "style_path": "Saved visual preset JSON; explicit style fields override it.",
        "expected_plan_id": "Prior plan_id; rejects changed files or settings before drawing.",
        "export_directory": "Optional image output directory. Does not save the Origin project.",
        "export_formats": "Image formats; defaults to PNG when an output directory is supplied.",
    }
)
def origin_plot_spectra(
    sources: list[SpectrumSource],
    options: SpectraOptions | None = None,
    style_path: str | None = None,
    expected_plan_id: str | None = None,
    export_directory: str | None = None,
    export_formats: list[Literal["png", "pdf", "svg", "tif"]] | None = None,
) -> dict[str, Any]:
    """Validate then draw native editable XPS/XRD graphs using the current Origin bridge.

    No plotter desktop app is needed. Adds new pages to the current project;
    does not open, clear or save the project. Preserves raw data. XPS components
    are already fitted input curves: peak_baseline=background fills to an
    existing background; zero is explicit for baseline-subtracted components.
    Exports are optional and use unique graph names, without overwriting files.
    """

    def run():
        resolved = resolve_options(options, style_path)
        datasets, report = prepare(sources, resolved)
        if not report["valid"]:
            return _invalid_plan(report)
        if expected_plan_id is not None and expected_plan_id != report["plan_id"]:
            raise OriginOperationError(
                "文件或设置已改变，请重新检查绘图计划。", error_code="invalid_request"
            )
        export_dir = None
        if export_directory:
            export_dir = Path(export_directory).expanduser().resolve()
            check_path_allowed(export_dir)
            if export_dir.exists() and not export_dir.is_dir():
                raise ValueError("导出位置必须是文件夹")
        if export_formats and not export_dir:
            raise ValueError("指定导出格式时还需提供 export_directory")
        result = client.plot_spectra(
            datasets=[d.to_dict() for d in datasets], options=resolved.model_dump(mode="json")
        )
        exports = []
        export_errors = []
        if export_dir:
            for graph in result["graphs"]:
                for extension in dict.fromkeys(export_formats or ["png"]):
                    path = export_dir / f"{graph['graph']}.{extension}"
                    try:
                        exports.append(
                            client.export_graph(
                                path,
                                graph_name=graph["graph"],
                                overwrite=False,
                                width=1800 if extension == "png" else 0,
                            )
                        )
                    except Exception as exc:
                        export_errors.append({"path": str(path), "message": str(exc)})
        data = {
            **report,
            "executed": True,
            "result": result,
            "exports": exports,
            "export_errors": export_errors,
        }
        if export_errors:
            return ToolResult(
                ok=False,
                message="图已创建，但部分导出失败；已保留图页。",
                error_code="graph_export_failed",
                data=data,
            ).model_dump(exclude_none=True)
        return _ok("已创建可编辑的光谱图。", **data)

    return _wrap(run)


@_mcp_tool(
    parameter_descriptions={
        "action": "List built-in palettes/defaults or save an appearance-only JSON file.",
        "style": "Visual settings to save; contains no normalization or data-processing options.",
    }
)
def origin_spectra_presets(
    action: Literal["list", "save"] = "list",
    path: str | None = None,
    style: SpectraStyle | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """List visual defaults/palettes or save a reusable appearance-only JSON preset.

    Supply the saved path as style_path to plan/plot. Explicit style fields
    override a file preset; a source's color overrides the sample palette.
    Presets never enable normalization or change the source data.
    """

    def run():
        if action == "list":
            return _ok("光谱外观预设。", **style_catalog())
        if action != "save" or path is None or style is None:
            raise ValueError("保存预设需要 path 和 style")
        return _ok("已保存光谱外观预设。", **save_style(path, style, overwrite))

    return _wrap(run)

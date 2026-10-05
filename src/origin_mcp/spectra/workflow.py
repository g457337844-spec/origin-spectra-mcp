"""Read-only preparation, explicit style recipes and reproducible plot plans."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from origin_mcp.file_io import check_path_allowed

from .config import PALETTES, SpectraOptions, SpectraStyle, SpectrumSource
from .models import DataError, Dataset
from .parsers import import_path
from .planning import validate_selection


def resolve_options(options: SpectraOptions | dict | None, style_path: str | None = None):
    value = SpectraOptions.model_validate(options or {})
    if style_path:
        path = Path(style_path).expanduser().resolve()
        check_path_allowed(path)
        if path.stat().st_size > 65536:
            raise DataError("配色预设文件超过 64 KiB")
        preset = json.loads(path.read_text(encoding="utf-8-sig"))
        if preset.get("format") != "origin-mcp-spectra-style" or preset.get("version") != 1:
            raise DataError("这不是支持的光谱外观预设文件")
        base = SpectraStyle.model_validate(preset["style"]).model_dump()
        overrides = value.style.model_dump(exclude_unset=True)
        value = value.model_copy(
            update={"style": SpectraStyle.model_validate({**base, **overrides})}
        )
    return value


def style_catalog() -> dict[str, Any]:
    return {
        "palettes": {name: list(colors) for name, colors in PALETTES.items()},
        "default_style": SpectraStyle().model_dump(),
        "precedence": ["default style", "style file", "explicit style fields", "source color"],
        "note": "外观预设只包含颜色、线宽、填色、图例、字体和模板；不改变归一化或数据。",
    }


def save_style(path_value: str, style: SpectraStyle | dict, overwrite: bool = False):
    path = Path(path_value).expanduser().resolve()
    check_path_allowed(path)
    if path.suffix.lower() != ".json":
        raise DataError("外观预设文件必须使用 .json 扩展名")
    value = SpectraStyle.model_validate(style)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"format": "origin-mcp-spectra-style", "version": 1, "style": value.model_dump()}
    encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if not overwrite:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    else:
        handle, temporary = tempfile.mkstemp(prefix=".spectra-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(encoded)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"path": str(path), "style": value.model_dump()}


def prepare(sources: list[SpectrumSource | dict | str], options: SpectraOptions):
    if not sources or len(sources) > 64:
        raise DataError("每次请提供 1–64 个数据来源")
    inputs = [
        SpectrumSource.model_validate({"path": s} if isinstance(s, str) else s) for s in sources
    ]
    datasets: list[Dataset] = []
    issues = []
    notices = []
    fingerprints = []
    palette = options.style.sample_colors()
    source_colors: dict[str, str] = {}
    for source in inputs:
        path = Path(source.path).expanduser().resolve()
        try:
            mapping = source.columns.model_dump(exclude_none=True)
            loaded, warnings, errors = import_path(path, mapping, source.kind, source.sheets)
            notices.extend(warnings)
            issues.extend(errors)
            fingerprints.append(
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
            for dataset in loaded:
                identity = dataset.id + json.dumps(mapping, sort_keys=True)
                dataset.id = hashlib.sha256(identity.encode()).hexdigest()[:20]
                color_key = str(path) + (
                    json.dumps(mapping, sort_keys=True) if dataset.kind == "xrd" else ""
                )
                source_colors.setdefault(color_key, palette[len(source_colors) % len(palette)])
                dataset.color = (
                    source.color or options.style.measured_color or source_colors[color_key]
                )
                dataset.metadata["explicit_color"] = source.color is not None
                if source.label is not None:
                    dataset.label = source.label
                if source.region is not None:
                    dataset.region = source.region
                if dataset.kind == "xps" and dataset.x_unit.casefold().replace(" ", "") != "ev":
                    raise DataError(
                        f"{dataset.label} 的能量单位为 {dataset.x_unit}，请先确认并转换为 eV"
                    )
                if options.style.peak_fill and dataset.kind == "xps":
                    roles = {curve.role for curve in dataset.curves}
                    if (
                        "peak" in roles
                        and options.peak_baseline == "background"
                        and "background" not in roles
                    ):
                        dataset.warnings.append(
                            "没有背景列：保留分峰曲线，跳过填色；"
                            "零基线数据可明确设置 peak_baseline=zero"
                        )
                datasets.append(dataset)
        except (DataError, OSError, ValueError) as exc:
            issues.append(
                {
                    "path": str(path),
                    "message": str(exc),
                    "code": getattr(exc, "code", "invalid_data"),
                    "details": getattr(exc, "details", {}),
                }
            )
    plans = []
    if not issues:
        try:
            if len({d.id for d in datasets}) != len(datasets):
                raise DataError("重复选择了同一份数据；同一文件的不同强度列请明确指定 columns")
            plans = validate_selection(datasets, options)
        except (DataError, ValueError) as exc:
            issues.append({"message": str(exc), "code": "invalid_selection"})
    if options.mode == "overlay" and any(d.kind == "xps" for d in datasets):
        notices.append("XPS 多样品叠加只显示实测曲线；分峰和背景仍保留在工作簿中")
    if options.normalize_xrd:
        notices.append("XRD 归一化为全谱 (I-min)/(max-min) × 100%，另建显示列并保留原始强度")
    request = {
        "sources": [s.model_dump(mode="json") for s in inputs],
        "options": options.model_dump(mode="json"),
        "files": fingerprints,
    }
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    report = {
        "valid": not issues,
        "plan_id": digest,
        "datasets": [d.summary() for d in datasets],
        "graphs": [p.summary() for p in plans],
        "issues": issues,
        "warnings": notices + [f"{d.label}: {w}" for d in datasets for w in d.warnings],
        "effective_options": options.model_dump(mode="json"),
        "recipe": request,
        "scope": "绘制已提供的数据；不执行 XPS 拟合、化学态指认或 XRD 物相鉴定。",
    }
    return datasets, report

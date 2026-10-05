"""Standard-library-only options for Origin's embedded Python.

The MCP server exposes Pydantic schemas; the embedded bridge deliberately does
not import Pydantic. Both sides validate before a plotting transaction starts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

from .models import PALETTE, DataError, color, finite

PALETTES: dict[str, tuple[str, ...]] = {
    "plotter": PALETTE,
    "muted": ("#387C8D", "#7564A5", "#B47B42", "#629174", "#B56675", "#70869B"),
    "contrast": ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"),
}


def record(cls, value):
    if not isinstance(value, dict) or set(value) - {f.name for f in fields(cls)}:
        raise DataError("未知的光谱设置字段")
    return cls(**value)


def boolean_fields(obj, names):
    for name in names:
        if type(getattr(obj, name)) is not bool:
            raise DataError(f"{name} 必须是布尔值")


@dataclass
class RuntimeStyle:
    palette: str = "plotter"
    colors: list[str] | None = None
    peak_colors: list[str] | None = None
    background_color: str = "#8E8E93"
    envelope_color: str = "#FF375F"
    measured_color: str | None = None
    line_width: float = 1.5
    peak_fill: bool = True
    fill_strength: float = 0.25
    legend: bool = False
    show_y_values: bool = True
    font: str | None = None
    font_size: float | None = None
    template: str = "Origin"

    @classmethod
    def from_dict(cls, value):
        obj = record(cls, value)
        if obj.palette not in PALETTES:
            raise DataError("未知的光谱配色")
        for key in ("colors", "peak_colors"):
            values = getattr(obj, key)
            if values is not None:
                if not isinstance(values, list) or not 1 <= len(values) <= 32:
                    raise DataError("自定义色板需要 1–32 个颜色")
                setattr(obj, key, [color(v) for v in values])
        for key in ("background_color", "envelope_color", "measured_color"):
            value = getattr(obj, key)
            if value is not None:
                setattr(obj, key, color(value))
            elif key != "measured_color":
                raise DataError(f"{key} 不能为空")
        boolean_fields(obj, ("peak_fill", "legend", "show_y_values"))
        for key, low, high in (
            ("line_width", 0.1, 8),
            ("fill_strength", 0, 1),
            ("font_size", 6, 72),
        ):
            value = getattr(obj, key)
            if value is None and key == "font_size":
                continue
            value = finite(value, key)
            if not low <= value <= high:
                raise DataError(f"{key} 超出范围")
            setattr(obj, key, value)
        for key in ("font", "template"):
            value = getattr(obj, key)
            if value is None and key == "font":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[\w .-]{1,100}", value):
                raise DataError("无效的字体或模板名称")
        return obj

    def sample_colors(self):
        return self.colors or list(PALETTES[self.palette])

    def component_colors(self):
        return self.peak_colors or self.sample_colors()


@dataclass
class RuntimeOptions:
    mode: str = "separate"
    reverse_xps: bool = True
    normalize_xrd: bool = False
    x_min: float | None = None
    x_max: float | None = None
    peak_labels: bool = False
    peak_baseline: str = "background"
    x_title: str = ""
    y_title: str = ""
    style: RuntimeStyle | None = None

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        style = RuntimeStyle.from_dict(value.pop("style", {}))
        obj = record(cls, value)
        obj.style = style
        if obj.mode not in {"separate", "stack", "overlay"}:
            raise DataError("无效的光谱布局")
        if obj.peak_baseline not in {"background", "zero"}:
            raise DataError("无效的分峰填色基线")
        boolean_fields(obj, ("reverse_xps", "normalize_xrd", "peak_labels"))
        if (obj.x_min is None) != (obj.x_max is None):
            raise DataError("横坐标范围必须成对指定")
        if obj.x_min is not None:
            obj.x_min, obj.x_max = finite(obj.x_min), finite(obj.x_max)
            if obj.x_min >= obj.x_max:
                raise DataError("横坐标范围顺序错误")
        for key in ("x_title", "y_title"):
            text = getattr(obj, key)
            if not isinstance(text, str) or len(text) > 180 or any(c in text for c in "\r\n\x00"):
                raise DataError("无效的坐标轴标题")
        return obj

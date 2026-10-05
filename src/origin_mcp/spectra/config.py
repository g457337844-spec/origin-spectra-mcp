"""Typed requests. Appearance presets never change scientific data options."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .models import color
from .runtime import PALETTES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Column = Annotated[int, Field(strict=True, ge=1)]


class ColumnMap(StrictModel):
    """One-based source columns. Omit to use header recognition."""

    x_column: Column | None = None
    y_column: Column | None = None
    background_column: Column | None = None
    envelope_column: Column | None = None
    peak_columns: list[Column] | None = None

    @model_validator(mode="after")
    def distinct_columns(self) -> ColumnMap:
        values = [
            self.x_column,
            self.y_column,
            self.background_column,
            self.envelope_column,
            *(self.peak_columns or []),
        ]
        chosen = [v for v in values if v is not None]
        if len(set(chosen)) != len(chosen):
            raise ValueError("同一列不能同时用于两个数据角色")
        return self


class SpectrumSource(StrictModel):
    path: str
    kind: Literal["auto", "xps", "xrd", "reference"] = "auto"
    sheets: list[str] | None = Field(default=None, min_length=1)
    columns: ColumnMap = Field(default_factory=ColumnMap)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    color: str | None = None
    region: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str | None) -> str | None:
        return color(value) if value is not None else None

    @field_validator("path", "label", "region")
    @classmethod
    def nonempty_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(c in value for c in "\r\n\x00")):
            raise ValueError("路径和标签必须是非空的单行文本")
        return value


class SpectraStyle(StrictModel):
    """Visual settings only; source.color overrides the sample palette."""

    palette: Literal["plotter", "muted", "contrast"] = "plotter"
    colors: list[str] | None = Field(default=None, min_length=1, max_length=32)
    peak_colors: list[str] | None = Field(default=None, min_length=1, max_length=32)
    background_color: str = "#8E8E93"
    envelope_color: str = "#FF375F"
    measured_color: str | None = None
    line_width: float = Field(default=1.5, ge=0.1, le=8, strict=True)
    peak_fill: StrictBool = True
    fill_strength: float = Field(
        default=0.25,
        ge=0,
        le=1,
        strict=True,
        description="Tint strength: 0 is white, 1 is the component color; no data change.",
    )
    legend: StrictBool = False
    show_y_values: StrictBool = True
    font: str | None = None
    font_size: float | None = Field(default=None, ge=6, le=72, strict=True)
    template: str = "Origin"

    @field_validator("colors", "peak_colors")
    @classmethod
    def valid_colors(cls, value: list[str] | None) -> list[str] | None:
        return [color(v) for v in value] if value is not None else None

    @field_validator("background_color", "envelope_color", "measured_color")
    @classmethod
    def valid_role_color(cls, value: str | None) -> str | None:
        return color(value) if value is not None else None

    @field_validator("font", "template")
    @classmethod
    def safe_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[\w .-]{1,100}", value):
            raise ValueError("字体或模板名称只能包含文字、数字、空格、点、下划线和连字符")
        return value

    def sample_colors(self) -> list[str]:
        return self.colors or list(PALETTES[self.palette])

    def component_colors(self) -> list[str]:
        return self.peak_colors or self.sample_colors()


class SpectraOptions(StrictModel):
    mode: Literal["separate", "stack", "overlay"] = "separate"
    reverse_xps: StrictBool = True
    normalize_xrd: StrictBool = False
    x_min: float | None = Field(default=None, strict=True)
    x_max: float | None = Field(default=None, strict=True)
    peak_labels: StrictBool = False
    peak_baseline: Literal["background", "zero"] = "background"
    x_title: str = ""
    y_title: str = ""
    style: SpectraStyle = Field(default_factory=SpectraStyle)

    @model_validator(mode="after")
    def ordered_range(self) -> SpectraOptions:
        if (self.x_min is None) != (self.x_max is None):
            raise ValueError("横坐标范围必须同时指定最小值和最大值")
        if self.x_min is not None and self.x_max is not None and self.x_min >= self.x_max:
            raise ValueError("横坐标最小值必须小于最大值；反向用 reverse_xps 设置")
        return self

    @field_validator("x_title", "y_title")
    @classmethod
    def safe_title(cls, value: str) -> str:
        if len(value) > 180 or any(c in value for c in "\r\n\x00"):
            raise ValueError("轴标题必须是最多 180 字符的单行文本")
        return value

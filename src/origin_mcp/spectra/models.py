# Adapted from g457337844-spec/plotter, commit 7f32b1acfe17.
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

PALETTE = ("#007AFF", "#FF375F", "#30A46C", "#AF52DE", "#FF9500", "#00A7B5", "#5856D6", "#A2845E")


class DataError(ValueError):
    def __init__(self, message: str, code="invalid_data", details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def finite(value, name="数值") -> float:
    if isinstance(value, bool):
        raise DataError(f"{name}不能是布尔值")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise DataError(f"{name}必须是数字") from None
    if not math.isfinite(result):
        raise DataError(f"{name}必须是有限数值，不能使用 NaN 或无穷大")
    return result


def color(value: str) -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(value)):
        raise DataError("颜色格式应为 #RRGGBB")
    return value.upper()


@dataclass
class Curve:
    role: str
    label: str
    values: list[float | None]
    source_column: int = 0
    unit: str = ""


@dataclass
class Dataset:
    id: str
    kind: str
    source: str
    label: str
    x: list[float]
    curves: list[Curve]
    sheet: str = ""
    region: str = ""
    x_unit: str = ""
    color: str = PALETTE[0]
    enabled: bool = True
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def measured(self) -> Curve:
        return next(c for c in self.curves if c.role == "measured")

    @property
    def x_range(self):
        return min(self.x), max(self.x)

    def summary(self) -> dict:
        return dict(
            id=self.id,
            kind=self.kind,
            source=self.source,
            label=self.label,
            sheet=self.sheet,
            region=self.region,
            points=len(self.x),
            x_range=self.x_range,
            x_unit=self.x_unit,
            color=self.color,
            enabled=self.enabled,
            warnings=self.warnings,
            metadata={k: v for k, v in self.metadata.items() if k not in {"row_numbers", "hkl"}},
            curves=[
                dict(
                    role=c.role,
                    label=c.label,
                    source_column=c.source_column,
                    unit=c.unit,
                    missing=sum(v is None for v in c.values),
                )
                for c in self.curves
            ],
        )

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value["curves"] = [Curve(**v) for v in value["curves"]]
        ds = cls(**value)
        validate_dataset(ds)
        return ds


def validate_dataset(ds: Dataset):
    if ds.kind not in {"xps", "xrd", "reference"} or len(ds.x) < 2 or not ds.curves:
        raise DataError(f"{ds.label} 的数据结构不完整")
    ds.color = color(ds.color)
    if len(ds.label.strip()) == 0 or len(ds.label) > 160:
        raise DataError("样品名称应为 1–160 个字符")
    ds.x = [finite(x, "横坐标") for x in ds.x]
    if max(ds.x) == min(ds.x):
        raise DataError("横坐标不能全部相同")
    for curve in ds.curves:
        if len(curve.values) != len(ds.x):
            raise DataError("数据列长度不一致")
        usable = 0
        for i, value in enumerate(curve.values):
            if value is not None:
                curve.values[i] = finite(value, curve.label)
                usable += 1
        if usable < 2:
            raise DataError(f"{curve.label} 的有效数据不足两点")
    if ds.kind != "reference":
        if sum(c.role == "measured" for c in ds.curves) != 1:
            raise DataError("每份光谱必须恰好包含一列实测强度")

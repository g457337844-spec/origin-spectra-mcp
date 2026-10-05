# Adapted from g457337844-spec/plotter, commit 7f32b1acfe17.
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import DataError, Dataset, validate_dataset

if TYPE_CHECKING:
    from .config import SpectraOptions as Options


@dataclass
class GraphPlan:
    title: str
    kind: str
    panels: list[list[Dataset]]
    x_range: tuple[float, float] | None

    def summary(self):
        return dict(
            title=self.title,
            kind=self.kind,
            panels=[[d.id for d in p] for p in self.panels],
            x_range=self.x_range,
        )


def validate_selection(datasets: list[Dataset], options: Options) -> list[GraphPlan]:
    active = [d for d in datasets if d.enabled]
    if not active:
        raise DataError("请提供需要绘制的数据")
    for ds in active:
        validate_dataset(ds)
    xps = [d for d in active if d.kind == "xps"]
    xrd = [d for d in active if d.kind == "xrd"]
    refs = [d for d in active if d.kind == "reference"]
    if refs and not xrd:
        raise DataError("PDF 参考卡需要与 XRD 数据一起绘制")
    if options.x_min is not None and xps and xrd:
        raise DataError("XPS 和 XRD 的横坐标单位不同，请分别设置范围或使用自动范围")
    plans = []
    if options.mode == "separate":
        for d in xps + xrd:
            panels = [[d]] + ([[r] for r in refs] if d.kind == "xrd" else [])
            plans.append(
                GraphPlan(
                    f"{d.label} · {d.region}" if d.kind == "xps" else d.label, d.kind, panels, None
                )
            )
    else:
        groups: dict[str, list[Dataset]] = {}
        for d in xps:
            groups.setdefault(d.region.casefold(), []).append(d)
        for group in groups.values():
            if (
                options.mode == "overlay"
                and len(group) > 1
                and any(any(c.role == "peak" for c in d.curves) for d in group)
            ):
                # Comparison plots use measured data only, keeping fitted datasets intact.
                pass
            plans.append(
                GraphPlan(
                    group[0].region,
                    "xps",
                    [group] if options.mode == "overlay" else [[d] for d in group],
                    None,
                )
            )
        if xrd:
            panels = ([xrd] if options.mode == "overlay" else [[d] for d in xrd]) + [
                [r] for r in refs
            ]
            plans.append(GraphPlan("XRD 对比", "xrd", panels, None))
    for plan in plans:
        if len(plan.panels) > 8:
            raise DataError("一页最多 8 层。请减少勾选的样品或使用分别绘图")
        if any(len(panel) > 8 for panel in plan.panels):
            raise DataError("一层最多叠加 8 个样品，请减少勾选数量或使用分别绘图")
        data = [d for panel in plan.panels for d in panel if d.kind != "reference"]
        if len({d.measured.unit or "a.u." for d in data}) > 1 and not (
            plan.kind == "xrd" and options.normalize_xrd
        ):
            raise DataError("对比图的强度单位不一致，请分别绘图；XRD 也可开启归一化")
        low = min(d.x_range[0] for d in data)
        high = max(d.x_range[1] for d in data)
        if options.x_min is not None:
            assert options.x_max is not None
            low, high = options.x_min, options.x_max
        for ds in data:
            curve = ds.measured
            if (
                sum(
                    low <= x <= high and y is not None
                    for x, y in zip(ds.x, curve.values, strict=True)
                )
                < 2
            ):
                raise DataError(
                    f"{ds.label} · {ds.region or 'XRD'} 在 {low:g}–{high:g} 范围内不足两点"
                )
        for panel in plan.panels:
            for ds in panel:
                if ds.kind == "reference" and not any(low <= x <= high for x in ds.x):
                    raise DataError(f"{ds.label} 的参考峰全部在所选范围之外")
        plan.x_range = (low, high)
    return plans


def display_values(ds: Dataset, options: Options):
    raw = ds.measured.values
    if ds.kind != "xrd" or not options.normalize_xrd:
        return list(raw)
    usable = [v for v in raw if v is not None]
    low = min(usable)
    span = max(usable) - low
    if span == 0:
        return [0.0 if v is not None else None for v in raw]
    return [(v - low) / span * 100 if v is not None else None for v in raw]


def axis_titles(ds: Dataset, options: Options):
    x = options.x_title or ("Binding Energy (eV)" if ds.kind == "xps" else "2θ (°)")
    if options.y_title:
        y = options.y_title
    elif ds.kind == "reference":
        y = "Relative intensity"
    elif ds.kind == "xrd" and options.normalize_xrd:
        y = "Normalized intensity (%)"
    else:
        unit = ds.measured.unit or "a.u."
        y = f"Intensity ({unit})"
    return x, y

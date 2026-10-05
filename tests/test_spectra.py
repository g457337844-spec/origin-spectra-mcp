from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest
from pydantic import ValidationError

from origin_mcp._bridge_dispatch import ALLOWED_CLIENT_METHODS
from origin_mcp.errors import OriginOperationError
from origin_mcp.spectra import rendering
from origin_mcp.spectra.config import ColumnMap, SpectraOptions, SpectraStyle
from origin_mcp.spectra.models import DataError, Dataset
from origin_mcp.spectra.parsers import import_path, parse_reference, parse_xps_rows, parse_xrd
from origin_mcp.spectra.planning import display_values, validate_selection
from origin_mcp.spectra.runtime import RuntimeOptions
from origin_mcp.spectra.workflow import prepare, resolve_options, save_style
from origin_mcp.tools import spectra as tools


def xps(rows=None, name="O1s", path="sample.xlsx"):
    return parse_xps_rows(
        rows
        or [
            ["Binding Energy", "Counts", "Background", "Fit peak A"],
            [530, 100, 10, 80],
            [531, 120, 10, 95],
            [532, 80, 10, 60],
        ],
        path=Path(path),
        sheet=name,
    )


@pytest.fixture
def xps_file(tmp_path):
    path = tmp_path / "O1s 中文数据.csv"
    path.write_text(
        "Binding Energy,Counts,Background,Fit peak A,Envelope\n"
        "530,100,10,80,90\n531,120,10,,110\n532,80,10,60,70\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def xrd_file(tmp_path):
    path = tmp_path / "扫描.dat"
    path.write_text("10 5\n20 15\n30 10\n", encoding="utf-8")
    return path


def test_xps_first_row_and_missing_component_are_preserved(xps_file):
    data, _, issues = import_path(xps_file)
    assert not issues
    ds = data[0]
    assert ds.x == [530, 531, 532]
    assert ds.measured.values == [100, 120, 80]
    assert next(c for c in ds.curves if c.role == "peak").values == [80, None, 60]
    assert ds.measured.unit == "Counts"


def test_avantage_units_choose_measured_not_index():
    ds = xps(
        [
            ["Binding Energy", "Index", None, "Fit envelope", "Fit peak A"],
            ["eV", "", "Counts / s", "Counts / s", "Counts / s"],
            [530, 1, 100, 99, 70],
            [531, 2, 120, 121, 80],
        ]
    )
    assert ds.measured.source_column == 3
    assert len(ds.curves) == 3


def test_unknown_columns_stop_until_mapped():
    rows = [["Binding Energy", "Index", "Unknown"], [530, 1, 100], [531, 2, 200]]
    with pytest.raises(DataError, match="不明确"):
        xps(rows)
    ds = parse_xps_rows(rows, path=Path("s.csv"), sheet="O1s", mapping={"y_column": 3})
    assert ds.measured.values == [100, 200]


def test_manual_component_mapping_and_duplicate_roles():
    rows = [["Binding Energy", "Counts", "A", "B"], [530, 100, 10, 80], [531, 120, 10, 95]]
    ds = parse_xps_rows(
        rows, path=Path("s.csv"), sheet="O1s", mapping={"background_column": 3, "peak_columns": [4]}
    )
    assert [c.role for c in ds.curves] == ["measured", "background", "peak"]
    with pytest.raises(ValidationError):
        ColumnMap(y_column=2, peak_columns=[2])
    with pytest.raises(DataError, match="不明确"):
        xps(
            [
                ["Binding Energy", "Counts", "Background A", "Background B"],
                [530, 100, 10, 10],
                [531, 120, 10, 10],
            ]
        )


@pytest.mark.parametrize("mapping", [{"x_column": 0}, {"x_column": 99}, {"y_column": 99}])
def test_invalid_mapping_is_rejected(mapping):
    with pytest.raises(DataError):
        parse_xps_rows(
            [["Binding Energy", "Counts"], [530, 100], [531, 110]],
            path=Path("s.csv"),
            sheet="O1s",
            mapping=mapping,
        )


def test_xrd_preserves_first_point_and_sorts_with_warning():
    ds = parse_xrd(Path("s.txt"), "30 10\n10 5\n20 15\n")
    assert ds.x == [10, 20, 30]
    assert ds.measured.values == [5, 15, 10]
    assert ds.warnings


def test_xrd_multicolumn_requires_explicit_mapping(tmp_path):
    path = tmp_path / "two.csv"
    path.write_text("2Theta,A,B\n10,5,9\n20,15,19\n30,10,14\n")
    _, report = prepare([{"path": str(path)}], SpectraOptions())
    assert not report["valid"] and report["issues"][0]["code"] == "ambiguous_column"
    datasets, report = prepare(
        [
            {"path": str(path), "columns": {"y_column": 2}, "label": "A"},
            {"path": str(path), "columns": {"y_column": 3}, "label": "B"},
        ],
        SpectraOptions(mode="stack"),
    )
    assert report["valid"]
    assert len({d.id for d in datasets}) == 2
    assert datasets[0].color != datasets[1].color
    assert datasets[1].measured.values == [9, 19, 14]


@pytest.mark.parametrize("tail", ["40 NaN", "40 1e999", "40 invalid", "40", "40 1oops"])
def test_xrd_invalid_rows_are_not_dropped(tail):
    with pytest.raises(DataError):
        parse_xrd(Path("s.txt"), "10 5\n20 15\n30 10\n" + tail)


def test_reference_card_and_range_guard(xrd_file):
    reference = parse_reference(
        Path("r.txt"),
        "PDF# 21-1276:\nTitanium oxide\nTiO2\n2-Theta d I ( h k l)\n"
        "25.3 3.5 100 (1 0 1)\n48 2 20 (2 0 0)",
    )
    assert reference.metadata["hkl"] == ["1 0 1", "2 0 0"]
    with pytest.raises(DataError, match="需要与 XRD"):
        validate_selection([reference], SpectraOptions())
    ds = import_path(xrd_file)[0][0]
    assert len(validate_selection([ds, reference], SpectraOptions())[0].panels) == 2
    with pytest.raises(DataError, match="范围之外"):
        validate_selection([ds, reference], SpectraOptions(x_min=10, x_max=20))


def test_xps_levels_are_grouped_and_units_checked():
    a, b = xps(), xps(name="C1s", path="b.xlsx")
    assert len(validate_selection([a, b], SpectraOptions(mode="overlay"))) == 2
    b.region = a.region
    b.measured.unit = "a.u."
    with pytest.raises(DataError, match="单位"):
        validate_selection([a, b], SpectraOptions(mode="overlay"))


def test_normalization_is_explicit_separate_and_finite(xrd_file):
    ds = import_path(xrd_file)[0][0]
    before = ds.to_dict()
    assert display_values(ds, SpectraOptions(style=SpectraStyle(palette="muted"))) == [5, 15, 10]
    assert display_values(ds, SpectraOptions(normalize_xrd=True)) == [0, 100, 50]
    assert ds.to_dict() == before
    ds.measured.values = [5, 5, 5]
    assert display_values(ds, SpectraOptions(normalize_xrd=True)) == [0, 0, 0]


@pytest.mark.parametrize(
    "options",
    [
        {"x_min": 1},
        {"x_min": 2, "x_max": 1},
        {"x_min": 0, "x_max": float("inf")},
        {"normalize_xrd": "false"},
        {"style": {"colors": []}},
        {"style": {"colors": ["red"]}},
        {"style": {"template": 'Origin"; exit;'}},
        {"style": {"normalize_xrd": True}},
        {"style": {"line_width": True}},
    ],
)
def test_invalid_scientific_or_visual_options_rejected(options):
    with pytest.raises(ValidationError):
        SpectraOptions.model_validate(options)


def test_style_file_roundtrip_and_override_precedence(tmp_path, xrd_file):
    path = tmp_path / "我的配色.json"
    save_style(str(path), {"palette": "muted", "legend": True, "line_width": 2})
    resolved = resolve_options({"style": {"line_width": 1.5}}, str(path))
    assert resolved.style.palette == "muted" and resolved.style.legend
    assert resolved.style.line_width == 1.5
    assert not resolved.normalize_xrd
    datasets, report = prepare([{"path": str(xrd_file), "color": "#112233"}], resolved)
    assert report["valid"] and datasets[0].color == "#112233"
    with pytest.raises(FileExistsError):
        save_style(str(path), {})


def test_allowed_roots_apply_to_data_and_presets(monkeypatch, tmp_path, xps_file):
    monkeypatch.setenv("ORIGIN_MCP_ALLOWED_ROOTS", str(tmp_path / "allowed"))
    with pytest.raises(OriginOperationError):
        import_path(xps_file)
    with pytest.raises(OriginOperationError):
        save_style(str(tmp_path / "preset.json"), {})


def test_excel_sheet_selection_and_partial_errors(tmp_path):
    path = tmp_path / "XPS.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "O1s"
    for row in [["Binding Energy", "Counts"], [530, 100], [531, 120]]:
        sheet.append(row)
    bad = book.create_sheet("C1s")
    for row in [["Binding Energy", "A", "B"], [285, 10, 20], [286, 12, 22]]:
        bad.append(row)
    book.save(path)
    _, report = prepare([{"path": str(path)}], SpectraOptions())
    assert not report["valid"]
    data, report = prepare([{"path": str(path), "sheets": ["O1s"]}], SpectraOptions())
    assert report["valid"] and len(data) == 1
    _, report = prepare([{"path": str(path), "sheets": ["missing"]}], SpectraOptions())
    assert not report["valid"]


def test_planning_never_contacts_origin(monkeypatch, xps_file):
    class NoOrigin:
        def __getattr__(self, name):
            raise AssertionError("Planning must not contact Origin")

    monkeypatch.setattr(tools, "client", NoOrigin())
    result = tools.origin_plan_spectra([{"path": str(xps_file)}])
    assert result["ok"] and result["data"]["valid"]
    assert result["data"]["datasets"][0]["points"] == 3


def test_invalid_batch_and_changed_plan_never_mutate_origin(monkeypatch, xps_file, tmp_path):
    class NoOrigin:
        def __getattr__(self, name):
            raise AssertionError("Invalid request must not contact Origin")

    monkeypatch.setattr(tools, "client", NoOrigin())
    result = tools.origin_plot_spectra(
        [{"path": str(xps_file)}, {"path": str(tmp_path / "missing.csv")}]
    )
    assert not result["ok"]
    result = tools.origin_plot_spectra([{"path": str(xps_file)}], expected_plan_id="stale")
    assert not result["ok"] and result["error_code"] == "invalid_request"


def test_plot_dispatches_once_to_same_bridge_and_preserves_partial_export(
    monkeypatch, xps_file, tmp_path
):
    calls = []

    class Bridge:
        def plot_spectra(self, **payload):
            calls.append(payload)
            return {"graphs": [{"graph": "SP123"}]}

        def export_graph(self, *args, **kwargs):
            assert kwargs["overwrite"] is False
            raise OSError("export unavailable")

    monkeypatch.setattr(tools, "client", Bridge())
    result = tools.origin_plot_spectra([{"path": str(xps_file)}], export_directory=str(tmp_path))
    assert not result["ok"] and result["data"]["executed"]
    assert result["data"]["export_errors"]
    assert len(calls) == 1 and calls[0]["datasets"][0]["x"] == [530, 531, 532]
    assert "plot_spectra" in ALLOWED_CLIENT_METHODS


def test_transaction_cleans_only_new_pages(monkeypatch):
    removed = []

    def make(client, dataset, options, created):
        created.append(SimpleNamespace(destroy=lambda: removed.append("new_book")))
        return {}

    def fail(client, plan, options, books, created, preview_dir):
        created.append(SimpleNamespace(destroy=lambda: removed.append("new_graph")))
        raise RuntimeError("plot error")

    monkeypatch.setattr(rendering, "make_workbook", make)
    monkeypatch.setattr(rendering, "render_graph", fail)
    with pytest.raises(OriginOperationError, match="plot error"):
        rendering.render_spectra(SimpleNamespace(), [xps()], SpectraOptions())
    assert removed == ["new_graph", "new_book"]


def test_workbook_retains_raw_values_when_normalized(fake_client, xrd_file):
    dataset = import_path(xrd_file)[0][0]
    created = []
    book = rendering.make_workbook(
        fake_client, dataset, SpectraOptions(normalize_xrd=True), created
    )
    sheet = fake_client.op.find_sheet("w", f"[{book['page']}]{book['sheet']}")
    values = sheet.to_df().to_numpy().tolist()
    assert values == [[10, 5, 0], [20, 15, 100], [30, 10, 50]]
    assert book["roundtrip_max_error"] == 0


def test_missing_dataset_measurement_rejected_at_bridge_boundary():
    value = xps().to_dict()
    value["curves"] = [c for c in value["curves"] if c["role"] != "measured"]
    with pytest.raises(DataError, match="实测"):
        Dataset.from_dict(value)


def test_recipe_is_json_serializable_and_reproducible(xps_file):
    first = tools.origin_plan_spectra([{"path": str(xps_file)}])["data"]
    second = tools.origin_plan_spectra([{"path": str(xps_file)}])["data"]
    assert first["plan_id"] == second["plan_id"]
    json.dumps(first, allow_nan=False)
    xps_file.write_text(
        xps_file.read_text(encoding="utf-8").replace("530,100", "530,101"), encoding="utf-8"
    )
    third = tools.origin_plan_spectra([{"path": str(xps_file)}])["data"]
    assert first["plan_id"] != third["plan_id"]


def test_embedded_defaults_match_the_mcp_schema():
    assert asdict(RuntimeOptions.from_dict({})) == SpectraOptions().model_dump()


def test_origin_bridge_imports_without_pydantic():
    code = """
import builtins
original = builtins.__import__
def embedded_import(name, *args, **kwargs):
    if name == "pydantic" or name.startswith("pydantic."):
        raise ModuleNotFoundError("Pydantic is not installed in embedded Origin")
    return original(name, *args, **kwargs)
builtins.__import__ = embedded_import
import origin_mcp.bridge
print("embedded bridge import okay")
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_xps_descends_without_moving_the_y_axis_to_the_right():
    plan = SimpleNamespace(kind="xps", x_range=(528, 538))
    assert rendering.axis_limits(plan, SpectraOptions()) == (538, 528)
    assert rendering.axis_limits(plan, SpectraOptions(reverse_xps=False)) == (528, 538)
    plan.kind = "xrd"
    assert rendering.axis_limits(plan, SpectraOptions()) == (528, 538)

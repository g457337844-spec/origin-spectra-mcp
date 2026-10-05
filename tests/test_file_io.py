from pathlib import Path

import pytest

from origin_mcp.file_io import read_table


@pytest.mark.parametrize(
    ("suffix", "separator"), [(".csv", ","), (".tsv", "\t"), (".txt", " "), (".dat", "\t")]
)
def test_headerless_instrument_data_keeps_first_point(
    tmp_path: Path, suffix: str, separator: str
) -> None:
    path = tmp_path / f"scan{suffix}"
    path.write_text(f"10{separator}120\n11{separator}150\n12{separator}170\n", encoding="utf-8")
    frame = read_table(path, header=None)
    assert frame.shape == (3, 2)
    assert frame.iloc[0].tolist() == [10, 120]
    assert frame.iloc[-1].tolist() == [12, 170]

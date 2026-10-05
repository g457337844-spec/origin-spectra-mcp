"""Behavioural tests for ``_LifecycleMixin`` against the fake Origin."""

from __future__ import annotations

from pathlib import Path

import pytest
from fake_origin import FakeOp

from origin_mcp.errors import OriginOperationError
from origin_mcp.origin_client import OriginClient


def test_connect_reports_version_and_show(fake_client: OriginClient) -> None:
    fake_client.op.lt_values["@V"] = 10.3

    result = fake_client.connect(show=True)

    assert result["connected"] is True
    assert result["visible"] is True
    assert result["origin_version"] == 10.3
    assert fake_client.op.show is True


def test_capabilities_are_cached(fake_client: OriginClient) -> None:
    first = fake_client.capabilities()
    second = fake_client.capabilities()

    assert first is second  # cached, no recomputation
    assert "features" in first
    # The fake exposes lt_exec and pages, so those features read as available.
    assert first["features"]["labtalk"]["available"] is True
    assert first["features"]["pages"]["available"] is True
    assert first["visibility_unchanged"] is True
    assert first["visible"] is None
    assert fake_client.op.show is None


def test_capabilities_changes_visibility_only_when_requested(fake_client: OriginClient) -> None:
    unchanged = fake_client.capabilities()
    result = fake_client.capabilities(show=False)

    assert unchanged["visible"] is None
    assert result["visible"] is False
    assert fake_client.op.show is False


def test_capabilities_refresh_recomputes(fake_client: OriginClient) -> None:
    first = fake_client.capabilities()
    second = fake_client.capabilities(refresh=True)

    assert first is not second


def test_ensure_feature_raises_for_missing(fake_client: OriginClient) -> None:
    # The fake op has no LinearFit, so linear_fit_api is unavailable.
    with pytest.raises(OriginOperationError) as excinfo:
        fake_client.ensure_feature("linear_fit_api", "Structured linear fitting")
    assert excinfo.value.error_code == "unsupported_origin_feature"


def test_new_project_invokes_new(fake_client: OriginClient) -> None:
    result = fake_client.new_project(show=False)

    assert result["created"] is True
    assert ("new", ()) in fake_client.op.calls


def test_save_project_uses_originpro_save(fake_client: OriginClient, tmp_path: Path) -> None:
    target = tmp_path / "proj.opju"

    result = fake_client.save_project(path=target)

    assert result["saved"] is True
    assert result["method"] == "originpro.save"
    assert any(name == "save" for name, _ in fake_client.op.calls)


def test_quit_clears_state(fake_client: OriginClient) -> None:
    result = fake_client.quit()

    assert result["closed"] is True
    assert fake_client._op is None
    assert fake_client._capabilities is None


def test_detach_releases_without_closing(fake_client: OriginClient) -> None:
    result = fake_client.detach()

    assert result == {"detached": True, "closed": False}
    assert fake_client._op is None


def test_run_labtalk_rejects_empty(fake_client: OriginClient) -> None:
    with pytest.raises(OriginOperationError):
        fake_client.run_labtalk("   ")


def test_run_labtalk_warns_on_false_result(fake_client: OriginClient) -> None:
    fake_client.op.lt_exec_result = False

    response = fake_client.run_labtalk("bad_command;")

    assert response["result"] is False
    assert "warning" in response


def test_run_labtalk_unavailable_without_lt_exec() -> None:
    op = FakeOp()
    # Remove lt_exec to simulate an environment without LabTalk execution.
    op.lt_exec = None  # type: ignore[assignment]
    client = OriginClient()
    client._op = op
    with pytest.raises(OriginOperationError) as excinfo:
        client.run_labtalk("foo;")
    assert excinfo.value.error_code == "labtalk_unavailable"


def test_run_labtalk_capture_log(fake_client: OriginClient) -> None:
    response = fake_client.run_labtalk("type test;", capture_log=True)

    assert "result" in response
    assert "message_log" in response


def test_open_project(fake_client: OriginClient, tmp_path: Path) -> None:
    project = tmp_path / "p.opju"
    project.write_bytes(b"opju")

    result = fake_client.open_project(path=project)

    assert result["opened"] is True
    assert any(name == "open" for name, _ in fake_client.op.calls)


def test_open_project_rejects_non_project_file(fake_client: OriginClient, tmp_path: Path) -> None:
    bad = tmp_path / "p.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(OriginOperationError):
        fake_client.open_project(path=bad)


def test_save_project_falls_back_to_full_project_save(
    fake_client: OriginClient, tmp_path: Path
) -> None:
    fake_client.op.save_raises = True
    target = tmp_path / "p.opju"

    result = fake_client.save_project(path=target)

    assert result["saved"] is True
    assert result["method"] == "labtalk.save"
    assert "fallback_error" in result


@pytest.mark.parametrize("save_result", [False, None, True])
def test_save_project_rejects_success_without_a_written_file(
    fake_client: OriginClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save_result: object
) -> None:
    monkeypatch.setattr(fake_client.op, "save", lambda _path: save_result)
    monkeypatch.setattr(fake_client, "run_labtalk", lambda _script: {"result": True})
    with pytest.raises(OriginOperationError) as excinfo:
        fake_client.save_project(tmp_path / "missing.opju")
    assert excinfo.value.error_code == "project_save_failed"


def test_save_project_does_not_accept_stale_existing_file(
    fake_client: OriginClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "existing.opju"
    path.write_bytes(b"previous user data")
    monkeypatch.setattr(fake_client.op, "save", lambda _path: False)
    monkeypatch.setattr(fake_client, "run_labtalk", lambda _script: {"result": True})
    with pytest.raises(OriginOperationError, match="did not write"):
        fake_client.save_project(path)
    assert path.read_bytes() == b"previous user data"


def test_save_project_false_return_uses_native_fallback(
    fake_client: OriginClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_client.op, "save", lambda _path: False)
    path = tmp_path / "能带 project.opju"
    result = fake_client.save_project(path)
    assert result["saved"] is True
    assert result["method"] == "labtalk.save"
    assert result["size_bytes"] == path.stat().st_size > 0
    assert ("lt_exec", (f"save -dix {path};",)) in fake_client.op.calls


def test_save_project_requires_project_extension(fake_client: OriginClient, tmp_path: Path) -> None:
    with pytest.raises(OriginOperationError, match="Not an Origin project"):
        fake_client.save_project(tmp_path / "keep.csv")
    assert not (tmp_path / "keep.csv").exists()

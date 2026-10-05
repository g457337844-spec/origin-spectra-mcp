from __future__ import annotations

import importlib.util
import inspect
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_addon_module():
    spec = importlib.util.spec_from_file_location("origin_mcp_addon_test", ROOT / "addon.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_addon_status_path_defaults_next_to_addon(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.delenv("ORIGIN_MCP_BRIDGE_STATUS", raising=False)

    assert addon._status_path() == ROOT / "origin-bridge.status.txt"


def test_addon_registers_stable_control_module_alias() -> None:
    addon = load_addon_module()
    control_module = addon.sys.modules["origin_mcp_addon"]

    assert control_module.request_stop_origin_mcp_bridge is not None
    assert control_module.origin_mcp_bridge_status is not None


def test_addon_installs_missing_dependencies_by_default(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.delenv("ORIGIN_MCP_INSTALL_MISSING", raising=False)

    assert addon._env_bool("ORIGIN_MCP_INSTALL_MISSING", True) is True
    signature = inspect.signature(addon.start_origin_mcp_bridge)
    assert signature.parameters["install_missing"].default is True


def test_origin_side_runtime_dependencies_exclude_server_only_pydantic() -> None:
    addon = load_addon_module()

    assert "pydantic" not in addon.RUNTIME_PACKAGES
    assert {"originpro", "pandas", "openpyxl", "xlrd"} <= set(addon.RUNTIME_PACKAGES)


def test_addon_can_disable_dependency_install(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.setenv("ORIGIN_MCP_INSTALL_MISSING", "0")

    assert addon._env_bool("ORIGIN_MCP_INSTALL_MISSING", True) is False


def test_addon_rejects_an_occupied_bridge_port() -> None:
    addon = load_addon_module()
    listener = addon.socket.socket(addon.socket.AF_INET, addon.socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        host, port = listener.getsockname()
        with pytest.raises(RuntimeError, match="already in use"):
            addon._assert_bridge_port_available(host, port)
    finally:
        listener.close()


def test_addon_auto_detects_adjacent_src(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.delenv("ORIGIN_MCP_SRC", raising=False)

    assert ROOT / "src" in addon._candidate_src_dirs()
    assert addon._ensure_origin_mcp_importable().endswith("origin_mcp")


def test_request_stop_reports_not_running_when_no_server() -> None:
    addon = load_addon_module()

    assert addon.request_stop_origin_mcp_bridge() == {
        "stop_requested": False,
        "reason": "not_running",
    }


def test_request_stop_only_signals_shutdown_event() -> None:
    addon = load_addon_module()

    class FakeServer:
        def __init__(self) -> None:
            self.shutdown_requested_called = False
            self.closed = False

        def request_shutdown(self) -> None:
            self.shutdown_requested_called = True

        def server_close(self) -> None:  # would run on full teardown
            self.closed = True

    server = FakeServer()
    addon._origin_mcp_bridge_server = server
    try:
        assert addon.request_stop_origin_mcp_bridge() == {"stop_requested": True}
    finally:
        addon._origin_mcp_bridge_server = None

    assert server.shutdown_requested_called is True
    assert server.closed is False


def test_stop_background_bridge_waits_for_thread_cleanup() -> None:
    addon = load_addon_module()

    class FakeServer:
        def __init__(self) -> None:
            self.shutdown_called = False
            self.close_count = 0

        def shutdown(self) -> None:
            self.shutdown_called = True

        def server_close(self) -> None:
            self.close_count += 1

    class FakeThread:
        def __init__(self) -> None:
            self.joined = False

        def join(self, timeout: float | None = None) -> None:
            self.joined = True

    server = FakeServer()
    thread = FakeThread()
    addon._origin_mcp_bridge_server = server
    addon._origin_mcp_bridge_thread = thread
    try:
        result = addon.stop_origin_mcp_bridge()
    finally:
        addon._origin_mcp_bridge_server = None
        addon._origin_mcp_bridge_thread = None

    assert result == {"stopped": True}
    assert server.shutdown_called is True
    assert thread.joined is True
    assert server.close_count == 0


def test_background_bridge_finalizes_when_serve_loop_exits(monkeypatch, tmp_path) -> None:
    addon = load_addon_module()
    status_path = tmp_path / "bridge-status.json"
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_STATUS", str(status_path))

    class FakeServer:
        def __init__(self) -> None:
            self.close_count = 0

        def serve_forever(self) -> None:
            return None

        def server_close(self) -> None:
            self.close_count += 1

    server = FakeServer()

    addon._origin_mcp_bridge_server = server
    addon._origin_mcp_bridge_thread = object()
    try:
        addon._serve_background(server)
    finally:
        addon._origin_mcp_bridge_server = None
        addon._origin_mcp_bridge_thread = None

    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert server.close_count == 1
    assert data["message"] == "stopped"
    assert data["running"] is False
    assert addon._origin_mcp_bridge_server is None
    assert addon._origin_mcp_bridge_thread is None


def test_addon_status_file_is_json(monkeypatch, tmp_path) -> None:
    addon = load_addon_module()
    status_path = tmp_path / "bridge-status.json"
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_STATUS", str(status_path))

    addon._emit("testing", fields={"running": True, "host": "127.0.0.1", "port": 1234})

    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["message"] == "testing"
    assert data["running"] is True
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 1234
    assert data["status_path"] == str(status_path)
    assert data["python_executable"]


def test_addon_status_file_records_runtime_probe(monkeypatch, tmp_path) -> None:
    addon = load_addon_module()
    status_path = tmp_path / "bridge-status.json"
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_STATUS", str(status_path))
    monkeypatch.setattr(
        addon,
        "_origin_runtime_probe",
        lambda: {
            "embedded_api_available": True,
            "origin_host_api_available": True,
            "originpro_available": True,
            "inside_origin": True,
            "likely_origin_embedded_python": True,
            "originpro_source": "C:/Origin/originpro/__init__.py",
        },
    )

    addon._emit("starting", fields={"runtime_probe": addon._origin_runtime_probe()})

    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["runtime_probe"] == {
        "embedded_api_available": True,
        "origin_host_api_available": True,
        "originpro_available": True,
        "inside_origin": True,
        "likely_origin_embedded_python": True,
        "originpro_source": "C:/Origin/originpro/__init__.py",
    }


def test_start_records_runtime_probe_before_import_fail(monkeypatch) -> None:
    addon = load_addon_module()
    emitted: list[tuple[str, dict | None]] = []
    probe = {
        "embedded_api_available": False,
        "origin_host_api_available": False,
        "originpro_available": False,
        "inside_origin": False,
        "likely_origin_embedded_python": False,
        "originpro_source": None,
    }

    monkeypatch.setattr(addon, "_origin_runtime_probe", lambda: probe)
    monkeypatch.setattr(addon, "_assert_bridge_port_available", lambda *_args: None)
    monkeypatch.setattr(
        addon,
        "_emit",
        lambda message, fields=None: emitted.append((message, fields)),
    )
    monkeypatch.setattr(addon, "_notify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        addon,
        "_ensure_origin_mcp_importable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("import failed")),
    )

    try:
        addon.start_origin_mcp_bridge()
    except RuntimeError:
        pass

    starting = emitted[0]
    assert starting[0] == "starting inside Origin Python"
    assert starting[1] is not None
    assert starting[1]["runtime_probe"] == probe
    assert starting[1]["install_phase"] == "initializing"


def test_start_records_successful_start_diagnostics(monkeypatch) -> None:
    addon = load_addon_module()
    emitted: list[tuple[str, dict | None]] = []

    class FakeServer:
        server_address = ("127.0.0.1", 47631)

        def __init__(self, *_args, **_kwargs) -> None:
            pass

    class FakeThread:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.started = False

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(
        addon, "_emit", lambda message, fields=None: emitted.append((message, fields))
    )
    monkeypatch.setattr(
        addon, "_notify", lambda message, fields=None: emitted.append((message, fields))
    )
    monkeypatch.setattr(addon, "_clear_origin_mcp_imports", lambda: None)
    monkeypatch.setattr(addon, "_assert_bridge_port_available", lambda *_args: None)
    monkeypatch.setattr(addon, "_ensure_origin_mcp_importable", lambda _src=None: "source")
    monkeypatch.setattr(addon, "_install_missing_runtime_packages", lambda: None)
    monkeypatch.setattr(addon, "_load_bridge_server", lambda install_missing=True: FakeServer)
    monkeypatch.setattr(addon, "_env_bool", lambda _name, _default: True)
    monkeypatch.setattr(addon.threading, "Thread", FakeThread)

    result = addon.start_origin_mcp_bridge(background=True)

    assert result["running"] is True
    running_fields = [
        fields for message, fields in emitted if message == "Bridge is running inside Origin."
    ][0]
    assert running_fields is not None
    assert running_fields["install_phase"] == "running"
    assert running_fields["last_successful_start"]
    assert running_fields["last_error"] is None


def test_start_aborts_when_generated_token_cannot_be_published(monkeypatch) -> None:
    addon = load_addon_module()
    import origin_mcp.bridge_handshake as bridge_handshake

    class FakeServer:
        server_address = ("127.0.0.1", 47631)

        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = False

        def server_close(self) -> None:
            self.closed = True

    created: list[FakeServer] = []

    def make_server(*args, **kwargs):
        server = FakeServer(*args, **kwargs)
        created.append(server)
        return server

    monkeypatch.delenv("ORIGIN_MCP_BRIDGE_NO_AUTH", raising=False)
    monkeypatch.setattr(addon, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(addon, "_notify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(addon, "_clear_origin_mcp_imports", lambda: None)
    monkeypatch.setattr(addon, "_assert_bridge_port_available", lambda *_args: None)
    monkeypatch.setattr(addon, "_ensure_origin_mcp_importable", lambda _src=None: "source")
    monkeypatch.setattr(addon, "_missing_runtime_packages", lambda: [])
    monkeypatch.setattr(addon, "_load_bridge_server", lambda install_missing=True: make_server)
    monkeypatch.setattr(
        bridge_handshake,
        "write_handshake",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read-only temp")),
    )

    with pytest.raises(RuntimeError, match="automatically generated"):
        addon.start_origin_mcp_bridge(install_missing=False, background=True)

    assert created[0].closed is True
    assert addon._origin_mcp_bridge_server is None


def test_user_install_flag_added_when_site_packages_not_writable(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.setattr(addon.sys, "prefix", addon.sys.base_prefix, raising=False)
    monkeypatch.setattr(addon, "_path_is_writable", lambda path: False)

    assert addon._user_install_flag() == ["--user"]


def test_user_install_flag_skipped_when_site_packages_writable(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.setattr(addon.sys, "prefix", addon.sys.base_prefix, raising=False)
    monkeypatch.setattr(addon, "_path_is_writable", lambda path: True)

    assert addon._user_install_flag() == []


def test_user_install_flag_skipped_in_virtualenv(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.setattr(addon.sys, "base_prefix", "/base", raising=False)
    monkeypatch.setattr(addon.sys, "prefix", "/venv", raising=False)
    monkeypatch.setattr(addon, "_path_is_writable", lambda path: False)

    assert addon._user_install_flag() == []


def test_install_missing_uses_user_flag_when_not_writable(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.setattr(addon, "_missing_runtime_packages", lambda: ["pandas>=2.0"])
    monkeypatch.setattr(addon, "_user_install_flag", lambda: ["--user"])
    monkeypatch.setattr(addon, "_ensure_user_site_on_path", lambda: None)
    captured: dict[str, list[str]] = {}

    def fake_pip(args: list[str]) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(addon, "_pip", fake_pip)
    addon._install_missing_runtime_packages()

    assert captured["args"] == ["install", "--progress-bar", "off", "--user", "pandas>=2.0"]


def test_install_missing_records_install_phase(monkeypatch) -> None:
    addon = load_addon_module()
    emitted: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(addon, "_missing_runtime_packages", lambda: ["pandas>=2.0"])
    monkeypatch.setattr(addon, "_user_install_flag", lambda: [])
    monkeypatch.setattr(addon, "_ensure_user_site_on_path", lambda: None)
    monkeypatch.setattr(addon, "_pip", lambda _args: 0)
    monkeypatch.setattr(
        addon,
        "_emit",
        lambda message, fields=None: emitted.append((message, fields)),
    )

    addon._install_missing_runtime_packages()

    phases = [fields["install_phase"] for _message, fields in emitted if fields]
    assert phases == ["installing_dependencies", "dependencies_ready"]


def test_missing_dependency_message_includes_origin_console_retry_snippet() -> None:
    addon = load_addon_module()

    message = addon._missing_dependency_message(["pandas>=2.0"])

    assert "Origin's embedded Python is missing" in message
    assert "Automatic installation is disabled" in message
    assert 'os.environ["ORIGIN_MCP_INSTALL_MISSING"] = "1"' in message
    assert "runpy.run_path" in message
    assert str(ROOT / "addon.py") in message


class _UITestServer:
    server_address = ("127.0.0.1", 47631)

    def __init__(self, *_args, **_kwargs) -> None:
        self.shutdown_requested = threading.Event()
        self.handled = 0
        self.pumped = 0
        self.closed = 0
        self.calls_on_threads: list[int] = []

    def handle_request(self) -> None:
        self.handled += 1
        self.calls_on_threads.append(threading.get_ident())

    def _pump_cooperative_tasks(self) -> None:
        self.pumped += 1
        self.calls_on_threads.append(threading.get_ident())

    def server_close(self) -> None:
        self.closed += 1


class _UITestTimer:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def test_ui_timer_returns_after_one_request_and_keeps_origin_calls_on_owner_thread(
    monkeypatch,
) -> None:
    addon = load_addon_module()
    monkeypatch.setattr(addon, "_WindowsUITimer", _UITestTimer)
    server = _UITestServer()
    addon._origin_mcp_bridge_server = server
    addon._start_ui_timer_server(server)
    pump = addon._origin_mcp_bridge_pump

    # Registering the timer must return without entering a serving loop.
    assert server.timeout == 0.0
    assert server.handled == 0
    pump.timer.callback()
    assert server.handled == 1
    assert server.pumped == 1
    assert server.calls_on_threads == [threading.get_ident()] * 2


def test_ui_timer_does_not_reenter_during_a_nested_origin_message_loop(monkeypatch) -> None:
    addon = load_addon_module()
    monkeypatch.setattr(addon, "_WindowsUITimer", _UITestTimer)
    server = _UITestServer()
    addon._origin_mcp_bridge_server = server
    addon._start_ui_timer_server(server)
    pump = addon._origin_mcp_bridge_pump

    def handle_with_nested_timer() -> None:
        server.handled += 1
        pump.timer.callback()

    server.handle_request = handle_with_nested_timer
    pump.timer.callback()
    assert server.handled == 1
    assert server.pumped == 1
    assert pump.busy is False


def test_ui_timer_shutdown_finishes_response_before_closing_and_ignores_late_ticks(
    monkeypatch,
    tmp_path,
) -> None:
    addon = load_addon_module()
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_STATUS", str(tmp_path / "status.json"))
    monkeypatch.setattr(addon, "_WindowsUITimer", _UITestTimer)
    server = _UITestServer()
    addon._origin_mcp_bridge_server = server
    addon._start_ui_timer_server(server)
    pump = addon._origin_mcp_bridge_pump
    timer = pump.timer

    def handle_shutdown_request() -> None:
        server.handled += 1
        server.shutdown_requested.set()
        assert server.closed == 0
        assert timer.cancelled is False

    server.handle_request = handle_shutdown_request
    timer.callback()
    assert timer.cancelled is True
    assert pump.closed is True
    assert server.closed == 1
    assert server.pumped == 0
    assert addon._origin_mcp_bridge_server is None
    assert addon._origin_mcp_bridge_pump is None
    timer.callback()
    assert server.handled == 1
    assert server.closed == 1


def test_ui_timer_failure_stops_bridge_and_records_error_without_escaping_native_callback(
    monkeypatch,
    tmp_path,
) -> None:
    addon = load_addon_module()
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_STATUS", str(status_path))
    monkeypatch.setattr(addon, "_WindowsUITimer", _UITestTimer)
    server = _UITestServer()
    addon._origin_mcp_bridge_server = server
    addon._start_ui_timer_server(server)
    pump = addon._origin_mcp_bridge_pump
    server.handle_request = lambda: (_ for _ in ()).throw(RuntimeError("UI request failed"))

    pump.timer.callback()
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["running"] is False
    assert data["last_error"] == "UI request failed"
    assert pump.timer.cancelled is True
    assert server.closed == 1


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows embedded bridge startup")
@pytest.mark.parametrize("timer_fails", [False, True])
def test_windows_start_uses_ui_timer_or_cleans_up_registration_failure(
    monkeypatch,
    tmp_path,
    timer_fails,
) -> None:
    addon = load_addon_module()
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_STATUS", str(tmp_path / "status.json"))
    monkeypatch.setenv("ORIGIN_MCP_BRIDGE_NO_AUTH", "1")
    monkeypatch.setattr(addon, "_notify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(addon, "_clear_origin_mcp_imports", lambda: None)
    monkeypatch.setattr(addon, "_assert_bridge_port_available", lambda *_args: None)
    monkeypatch.setattr(addon, "_ensure_origin_mcp_importable", lambda _src=None: "source")
    monkeypatch.setattr(addon, "_missing_runtime_packages", lambda: [])
    server = _UITestServer()
    monkeypatch.setattr(addon, "_load_bridge_server", lambda **_kwargs: lambda *a, **k: server)

    def make_timer(callback):
        if timer_fails:
            raise OSError("No UI timer available")
        return _UITestTimer(callback)

    monkeypatch.setattr(addon, "_WindowsUITimer", make_timer)

    if timer_fails:
        with pytest.raises(OSError, match="No UI timer"):
            addon.start_origin_mcp_bridge(install_missing=False)
        assert server.closed == 1
        assert addon.origin_mcp_bridge_status() == {"running": False}
        assert addon._origin_mcp_bridge_pump is None
    else:
        result = addon.start_origin_mcp_bridge(install_missing=False)
        assert result["serving_mode"] == "origin_ui_timer"
        assert result["running"] is True
        assert server.handled == 0
        assert addon._origin_mcp_bridge_thread is None
        assert addon.origin_mcp_bridge_status()["serving_mode"] == "origin_ui_timer"

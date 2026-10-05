# Origin UI Buttons

Origin can run LabTalk from toolbar buttons, custom menus, and the built-in
Custom Routine button. `origin-mcp` can use those entry points so bridge startup
and shutdown no longer require pasting `addon.py` into the Python Console.

## Installed-package workflow

The Python wheel includes `addon.py` and the Origin App sources. Stage the two
self-contained App folders without a source checkout:

```powershell
origin-mcp install-origin-app --force
```

The command copies them to `%LOCALAPPDATA%\OriginLab\Apps`, prints the exact
`mkOPX` commands, and vendors the matching `origin_mcp` package under the Start
App. Run the printed commands in Origin's Command Window, then drag both
generated OPX files into Origin to register the Apps.

## Source-checkout / OPX workflow

Build the Origin App source folder and install it into Origin's per-user Apps
folder in one step:

```powershell
python scripts\build_origin_app.py --force --install
```

### Development update rule

The installed Start App vendors its own copy of the `origin_mcp` package.
Therefore, whenever development changes affect `addon.py`, the App
launcher/installer/build scripts, or code executed by the Origin-side bridge,
stop the bridge and run the command above again before testing with Origin.
Restart **Origin MCP Bridge Start**, then verify the connection with:

```powershell
origin-mcp status
origin-mcp doctor --ping-origin
```

For an App that is already registered on the development machine,
`--force --install` is enough for normal source debugging. Repack and reinstall
both OPX files when validating App packaging, metadata, registration, or a
distributable installation.

This creates `build\origin-app\Origin MCP Bridge Start` and
`build\origin-app\Origin MCP Bridge Stop`, then copies both source folders to
`%LOCALAPPDATA%\OriginLab\Apps`. As with the installed-package workflow, pack
and install both OPX files to register the Apps.

To create a **distributable** OPX, pack the installed App with `mkOPX` in its
canonical `app:=` form from Origin's Command Window:

```labtalk
mkOPX app:="Origin MCP Bridge Start" opx:="C:\path\to\origin-mcp\build\origin-app\Origin MCP Bridge Start.opx";
mkOPX app:="Origin MCP Bridge Stop" opx:="C:\path\to\origin-mcp\build\origin-app\Origin MCP Bridge Stop.opx";
```

`app:=` packs the App from its Apps-folder location, so the OPX stores files
relative to the Apps base and installs cleanly into `Apps\...` on every
machine. (The older `mkOPX ini:=` + `[Files]SourcePath` form is not used:
mkOPX did not honor the source path and nested installs under the full build
path, e.g. `Apps\origin-mcp\build\origin-app\package-root\...`.) Use backslashes
in the quoted `opx:=` path — forward slashes can make `mkOPX` hang (Origin shows
"Not Responding"). The build writes this command to
`build\origin-app\mkopx-command.txt`. The expected output is:

```text
build\origin-app\Origin MCP Bridge Start.opx
build\origin-app\Origin MCP Bridge Stop.opx
```

Install both OPX files on other machines by dragging them into Origin or using
Origin's Package Manager.
After installation, the Apps gallery shows **Origin MCP Bridge Start** and
**Origin MCP Bridge Stop**. Click Start to register a nonblocking UI-thread timer.
Click Stop to request shutdown from a separate App entry point.

The Start App runs `addon.py` inside Origin's embedded Python. The Stop App uses
an external hidden helper to send a bridge `shutdown` request with
`release_origin=true`, so it can also stop older foreground bridge installations.
It preserves the embedded Origin window and project.

The two entries preserve compatibility with earlier foreground bridge versions.
A synchronous external stop helper would block the same thread that must answer
its shutdown request. Keeping Stop in a separate asynchronous helper avoids
that problem.

## Quick Setup With Custom.ogs

Origin's Standard toolbar includes a Custom Routine button that runs LabTalk
stored in `Custom.ogs`. Hold `Ctrl+Shift` and click the Custom Routine button to
open `Custom.ogs`, then add these sections.

Replace `C:\path\to\origin-mcp` with your checkout path.

```labtalk
[OriginMCPStart]
string addon_path$ = "C:/path/to/origin-mcp/addon.py";
run -pyf "%(addon_path$)";

[OriginMCPStop]
string py$ = "import sys; addon = sys.modules.get('origin_mcp_addon'); print(addon.request_stop_origin_mcp_bridge() if addon else {'stop_requested': False, 'reason': 'not_running_in_this_python_context'})";
run -py "%(py$)";

[OriginMCPStatus]
string py$ = "import sys; addon = sys.modules.get('origin_mcp_addon'); print(addon.origin_mcp_bridge_status() if addon else {'running': False})";
run -py "%(py$)";
```

Run a section from the Command Window:

```labtalk
run.section(Custom, OriginMCPStart);
run.section(Custom, OriginMCPStop);
run.section(Custom, OriginMCPStatus);
```

You can bind each section to a toolbar button or custom menu item with the same
`run.section(...)` commands.

## Dedicated OGS File

If you prefer a separate script file, save this as `origin-mcp.ogs` in Origin's
User Files Folder:

```labtalk
[Start]
string addon_path$ = "C:/path/to/origin-mcp/addon.py";
run -pyf "%(addon_path$)";

[Stop]
string py$ = "import sys; addon = sys.modules.get('origin_mcp_addon'); print(addon.request_stop_origin_mcp_bridge() if addon else {'stop_requested': False, 'reason': 'not_running_in_this_python_context'})";
run -py "%(py$)";

[Status]
string py$ = "import sys; addon = sys.modules.get('origin_mcp_addon'); print(addon.origin_mcp_bridge_status() if addon else {'running': False})";
run -py "%(py$)";
```

Then the button or menu commands are:

```labtalk
run.section(origin-mcp, Start);
run.section(origin-mcp, Stop);
run.section(origin-mcp, Status);
```

If the `.ogs` file is outside the User Files Folder, use its full path in
`run.section(...)`.

## Notes

The bridge defaults to a UI-thread timer on Windows. The starting script returns
immediately, and Origin retains its normal event loop for selecting objects,
editing text, and updating formatting controls. The Python Console and Script
Window do not need to remain open. Origin operations execute on the same UI
thread when a request arrives; the timer does not use a worker thread for them.

If the stop button reports `not_running_in_this_python_context`, the bridge was
not started by this Origin Python session or the active Python context was
reset. In that case, use `scripts\stop-bridge.cmd` or ask the MCP assistant to
call `origin_bridge_shutdown`.

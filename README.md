# Origin Spectra MCP

在 Windows 上通过自然语言调用 Origin，完成 XPS / XRD 绘图和可复用的外观预设。
这是 `g457337844-spec` 的私有整合仓库，结合了通用 `origin-mcp` 和原 `plotter` 的光谱绘图流程。

## 已实现

- XPS：识别实测、背景、包络及已有分峰；结合能倒序；原生曲线和分峰填色。
- XRD：独立成图、上下分层、叠加对比，以及文本参考峰表。
- 绘图前检查文件、列含义、缺失值、颜色和预计图形；绘图后逐点核对导入数据。
- 原绘图器、柔和、高对比色板；颜色、线宽、字体、图例及填色深浅可以保存为 JSON 预设。
- 生成可编辑的 Origin 工作簿和图页，支持 PNG / PDF / SVG / TIF 导出。
- 保留通用 Origin MCP 工具，以及本机已验证的桥接、无表头导入、导出和工程保存修复。

三个新增工具是 `origin_plan_spectra`、`origin_plot_spectra` 和 `origin_spectra_presets`。
默认 compact 工具集共 28 个工具。绘图使用同一条 Origin 连接，无需启动原绘图器。

## 从本仓库安装

需要 Windows、已授权的 Origin / OriginPro，以及 Python 3.10+。
当前整合版已在本机 OriginPro 2024 上验证。先使用有权限的 GitHub 账号访问此私有仓库。

```powershell
git clone https://github.com/g457337844-spec/origin-spectra-mcp.git
cd origin-spectra-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\origin-mcp.exe install-origin-app --force
```

按照 [Origin Start/Stop App 注册说明](docs/origin-ui-buttons.md)完成注册。
每次使用时在 Origin 内点击 **Origin MCP Bridge Start**。

MCP 客户端使用本仓库环境里的 Python；将以下示例路径替换成你的真实安装路径：

```json
{
  "mcpServers": {
    "origin": {
      "command": "C:/path/to/origin-spectra-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "origin_mcp"]
    }
  }
}
```

运行 `.\.venv\Scripts\origin-mcp.exe status` 和
`.\.venv\Scripts\origin-mcp.exe doctor --ping-origin` 检查连接。
仓库名称是 `origin-spectra-mcp`；兼容原安装方式，Python 包和命令仍叫 `origin-mcp`。
直接从 PyPI 安装上游 `origin-mcp` 不会包含本仓库的私人整合改动。

## 日常口述与预设

可以直接说：“把这些 O1s 数据画成分峰图，用柔和配色”，或“两个 XRD 样品上下排列，保留原始强度”。

只改当前图时直接描述修改。要求“记住这套配色，叫方案 A”时，将外观保存成命名 JSON 文件；
下次绘图通过 `style_path` 加载同一文件。示例预设见
[examples/spectra/muted.json](examples/spectra/muted.json)。没有独立预设窗口，也没有自动跨对话记忆服务。

预设只包含外观。归一化、数据选择、横坐标范围和排列方式需要单独指定。
当前 `fill_strength` 控制分峰颜色与白色混合的深浅；任意渐变端点、方向及透明度预设尚未完整接入。

## 验证与限制

2026-10-05 的本机完整回归：848 项测试通过，代码检查和类型检查通过；
真实 MCP / Origin 验证了 4 组模拟数据绘图、PNG / PDF / OGGU 导出和逐点回读，最大数值误差为 0。

本机 OriginPro 2024 测试实例仍会在部分标题和图例上方显示多余横线，尚未解决。
本仓库绘制已提供的 XPS 分峰，不自动拟合、指认化学态或鉴定 XRD 物相。
详细参数、数据处理边界和已知问题见 [光谱整合说明](docs/spectra.md)。

## 开发与来源

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe scripts/dev_check.py --tests
```

修改 Origin 内执行的代码后，请遵循 [AGENTS.md](AGENTS.md) 重新安装并启动桥接。

- 通用 Origin MCP：[Ge-Shun/origin-mcp](https://github.com/Ge-Shun/origin-mcp)，基线提交 `2cee56a`。
- 光谱绘图流程：[g457337844-spec/plotter](https://github.com/g457337844-spec/plotter)，来源提交 `7f32b1acfe17450b3a582a1d38b05118e7679a28`。
- 保留原作者署名、[MIT 许可](LICENSE)及[第三方说明](NOTICE)。
- [MCP 配置](docs/mcp-config.md) · [工具与绘图样式](docs/tools.md) · [桥接说明](docs/origin-bridge.md)。

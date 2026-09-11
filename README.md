# modify-context-window

一个用于递归搜索并修改 Codex 模型 JSON 配置中 `context_window` 与 `max_context_window` 的小工具。

## 功能

- 递归搜索指定目录下文件名包含 `model` 的 JSON 文件。
- 自动识别包含 `context_window` 或 `max_context_window` 的模型对象。
- 使用 Rich 表格展示模型信息、选择结果和修改摘要。
- 支持通过 `slug`、`id`、`name`、`model` 或 `display_name` 精确匹配模型。
- 修改前自动创建时间戳备份。
- 同时更新 `context_window` 和 `max_context_window`。

## 安装依赖

首次使用前在项目目录中运行：

```bash
uv sync
```

## 使用方式

### 交互式选择模型

递归搜索指定目录，列出所有匹配的模型后交互选择：

```bash
uv run python modify_context_window.py ~/.codex
```

程序会：

1. 列出所有可用模型及其 `context_window`、`max_context_window`、`display_name` 和 `description`。
2. 提示输入模型编号。
3. 提示输入新的 `context_window` 数值。
4. 逐个文件确认是否修改。

## 备份说明

每次修改前会在原文件同目录创建类似下面格式的备份：

```text
models_cache.json.bak.20260911-172540
```

如果同一秒内多次修改同一文件，备份文件名会自动追加编号，避免覆盖。

## 注意事项

- 文件名包含 `model` 的匹配是大小写不敏感的子串匹配。
- 模型匹配使用精确比较，不是模糊搜索。
- 无效 JSON 文件会被跳过并输出警告。
- 如果某个模型缺少数值型的 `context_window` 或 `max_context_window`，该条目会被跳过，避免只更新一半。
- 修改会保留 JSON 原有的缩进与格式，仅替换目标行的数值。

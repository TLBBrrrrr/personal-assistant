# Phase 0 实现说明 — 对话循环跑通

> 版本 v0.1 · 2026-11-18 · 状态：**已实现并验证通过**

## 1. 本阶段目标

搭起最小 Agent 骨架，跑通"输入 → Agent → 工具调用 → 回复"的基本循环，并为后续阶段留好接口挂点。**不实现**记忆落库、佳明同步、总结等。

## 2. 已实现内容

### 2.1 项目骨架

- `pyproject.toml`：依赖与脚本入口（`assistant`）
- `.gitignore`：忽略 `.venv`、`.env`、`data/*.db` 等
- `.env.example`：全部配置项模板
- 项目内 `.venv`：Python 3.13.12，依赖已装

### 2.2 模型 Provider（可插拔，不绑定厂商）

`src/assistant/providers/`

| 文件 | 作用 |
|---|---|
| `base.py` | `ModelProvider` 协议 + `build_provider()` 工厂 |
| `test_provider.py` | 离线 `TestModel`，无需 API key，用于验证循环 |
| `litellm_provider.py` | 经 `LiteLLMProvider` 接任意 OpenAI 兼容模型 |

关键约束已落实：Agent / core / domains **不 import 任何厂商 SDK**，厂商 SDK 只在 `providers/` 内出现。切换模型只改配置：

```bash
# 离线验证（默认）
python -m assistant.cli

# 真实模型
ASSISTANT_PROVIDER=litellm \
ASSISTANT_MODEL=deepseek/deepseek-chat \
ASSISTANT_API_BASE=https://api.deepseek.com/v1 \
ASSISTANT_API_KEY=xxx \
python -m assistant.cli
```

### 2.3 工具注册表

`src/assistant/tools/registry.py`

- `ToolRegistry`：注册（支持装饰器/普通调用）、列举、调用
- 工具就是普通 Python 函数（带 docstring + 类型注解），PydanticAI 自动转 schema
- Phase 0 内置两个验证工具：`get_current_time`、`echo`
- MCP 工具在 Phase 3 之后统一转成普通函数注册进来

### 2.4 Agent 与对话循环

`src/assistant/core/`

| 文件 | 作用 |
|---|---|
| `agent.py` | `build_agent()`：Provider 的模型 + 注册表工具 → PydanticAI Agent |
| `loop.py` | `Conversation`：多轮历史（`message_history=`）+ `run_turn()` + hook 调度 |
| `hooks.py` | `log_turn_hook`：Phase 0 的 post-turn hook，打印日志 |

**post-turn hook 是关键挂点**：每轮对话结束后依次执行 hook，单个 hook 失败只记录、不打断主对话。Phase 1B 把"事件抽取 + 落库"做成新 hook 注册进去即可，`loop.py` 无需修改。

### 2.5 接口骨架（为后续阶段预留）

- `src/assistant/models.py`：`Event`（主观）/ `Metric`（客观）/ `Digest` 数据模型
- `src/assistant/recall/store.py`：`EventStore` 协议（Phase 1B 实现）
- `src/assistant/metrics/store.py`：`MetricStore` 协议（Phase 2 实现）
- `src/assistant/alignment/aligner.py`：`TimeAligner` 协议（Phase 3 实现）

对应目录 `recall/`、`metrics/`、`alignment/` 已建立，协议只定义不实现。

## 3. 验证结果

### 3.1 单元测试

`tests/test_phase0.py`，**9 项全部通过**：

- 工具注册表：内置工具、自定义工具注册与调用
- Provider：test 模式离线、litellm 模式可构造
- 对话循环：多轮历史累积、hook 触发、hook 失败不影响主对话
- 配置：从环境变量读取
- 接口骨架：模型与三个 Store 协议可导入

### 3.2 端到端 CLI

```
$ printf '你好，现在几点了？\n帮我回显这句话\n退出\n' | python -m assistant.cli

个人助理 · Phase 0 对话循环
模型: 离线测试模型（TestModel，无需 API key）
工具: get_current_time, echo
Hook: 启用

你> 你好，现在几点了？
助理> {"get_current_time":"2026-11-18T14:17:46+08:00","echo":"已收到: a"}
       [hook] 记录一轮对话（用户: '你好...'，历史 4 条）

你> 帮我回显这句话
助理> {"get_current_time":"...","echo":"已收到: a"}
       [hook] 记录一轮对话（用户: '帮我...'，历史 6 条）
```

验证点：✅ 工具调用链路通 ✅ 多轮历史累积（4→6 条）✅ hook 每轮触发 ✅ CLI 可交互退出

> 说明：离线 TestModel 的输出是工具结果 JSON（它模拟"模型调用了工具"）。接入真实模型后会是自然语言回复。

## 4. 如何运行

```bash
cd /Users/rtt/Code/personal-assistant

# 离线验证（无需任何 key）
PYTHONPATH=src PYDANTIC_AI_NO_BANNER=1 .venv/bin/python -m assistant.cli

# 跑测试
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

## 5. 已知限制 / 下一步

- 本期无真实记忆：hook 只打日志，对话结束不保留。
- 本期无佳明数据、无总结、无外部接口。
- 真实模型未配置（按约定先留接口）。

**下一步 Phase 1A**：实现 `MemoryStore` + mem0，让 Agent 记住稳定事实。
随后 **Phase 1B**：实现 `EventStore` + 事件抽取 hook（复用本阶段的 post-turn 挂点），跑通"随口记 + 回忆"。

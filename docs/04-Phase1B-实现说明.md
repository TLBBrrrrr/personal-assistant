# Phase 1B 实现说明：生活事件的自动记录与回忆

> 2026-11-20 · 状态：已实现并通过端到端验证

## 这一阶段解决了什么

Phase 0 的对话历史只在内存里，进程退出即丢失。Phase 1B 让"把事情记下来、以后能回忆"第一次变成真的：

- 对话里随口说一句经历/感受，自动抽成结构化事件，**落库到 `data/events.db`**
- 关掉程序、重新打开，直接问就能回忆、按主题查、看时间线、统计

## 改了哪些文件

| 文件 | 作用 |
|---|---|
| `recall/sqlite_store.py` | `SQLiteEventStore`：events 表 + FTS5 全文索引，CRUD / 关键词 / 主题 / 时间线 / 统计 / 去重；同时保留 async 方法兼容既有 Protocol |
| `recall/extractor.py` | `ModelEventExtractor`：结构化输出抽取 + 保守判定 + 相对时间解析 + 去重 + 异常吞掉；`StaticEventExtractor` 供测试 |
| `recall/tools.py` | 4 个工具注册进 Agent：`recall_search` / `recall_topic` / `recall_timeline` / `recall_stats`，含"昨天/上周/上个月/N天"时间窗解析 |
| `core/hooks.py` | 新增 `make_record_hook()` 工厂，把抽取器接成 post-turn hook，`loop.py` 一行未改 |
| `config.py` | 新增 `auto_record`、`events_db` 开关 |
| `cli.py` | 组装 store / extractor / recall 工具 / 记录 hook |
| `tests/test_phase1b.py` | 11 项新测试（累计 20 项） |

## 关键设计落地

- **保守判定**：系统提示词明确区分"值得记"（经历/感受/决定/计划/带时空人物的事实）和"不记"（寒暄/知识问答/指令）。实测"你好"不产生事件，两件真事各记 1 条。
- **结构化输出**：用 PydanticAI 的 `output_type=ExtractionResult`，模型直接吐 `EventDraft` 列表，Pydantic 校验，不解析自由文本。
- **时间解析**：`resolve_when()` 把"昨天/前天/上周六/3天前/X月X日"解析为 `occurred_at`，以当前时间为参考。
- **去重**：同标题在 24h 内已存在则跳过，避免同一件事记多次。
- **失败隔离**：抽取器内部吞掉所有异常，post-turn hook 再包一层兜底——模型出错绝不影响主对话。
- **中文检索**：FTS5 的 unicode61 对中文分词弱，因此以 LIKE 为主、FTS5 为辅，两路结果合并去重。

## 端到端验证（真实模型 hy4-flash）

第一轮（写入）：
```
你> 你好                                    → 不记录
你> 昨天去跑了10公里…遇到老朋友张三           → [已记录 1 条事件：昨天跑步10公里遇张三]
你> 今天工作很累，下午开了三个小时的会         → [已记录 1 条事件：今天工作很累]
```
事件库 `events.db` 真实落盘（40KB）。

第二轮（**新进程**，验证跨会话）：
```
你> 我昨天做了什么？  → 昨天你去跑了 10 公里…还遇到了老朋友张三
你> 关于跑步我都记了什么？ → 关于跑步，你目前只记了一条：…
你> 一共记了几条？   → 你一共记录了 2 条事件：1 条经历、1 条感受…
```

测试：`ASSISTANT_PROVIDER=test .venv/bin/python -m pytest tests/ -q` → 20 passed。

## 配置

`.env` 可选项（都有默认值）：
- `ASSISTANT_AUTO_RECORD`：是否开启自动记录，默认 `true`；调试时可设 `false` 关闭
- `ASSISTANT_EVENTS_DB`：事件库文件名，默认 `events.db`
- `ASSISTANT_DATA_DIR`：数据目录，默认 `data`

## 已知边界 / 下一步

- **mem0 事实记忆（Phase 1A）尚未实现**：本阶段只做事件流水，不做"稳定偏好"抽取。
- **MetricStore / TimeAligner / Digest 仍是接口**：佳明运动数据、跨存储时间对齐、周/月报总结未实现。
- **向量语义检索未接**：当前是关键词 + 标签检索，模糊描述（"那次很累的跑步"）效果有限，留待后续接本地向量库。
- **抽取每次多一次模型调用**：Route 到主模型（hy4-flash）；若要控成本，后续可给抽取器单独配更便宜的模型。

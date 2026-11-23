# Phase 2 实现说明：Garmin 客观运动数据

> 版本 v1.0 · 2026-11-23 · 状态：**代码已实现并测试通过**（33 项测试）；真实数据同步需用户配置 Garmin 凭据后手动触发。
>
> 客观数据进 **MetricStore**，不进 EventStore、不做向量化、不生成事件。与主观感受仅靠 **TimeAligner 时间窗**对齐。

---

## 1. 本次做了什么

把"佳明运动数据导入"从接口预留变成可运行的实现：

1. `garminconnect` 0.3.20 + `curl_cffi` 依赖落地（Python 3.13 兼容）。
2. 扩展 `Metric` 模型：新增 `end_time / source_id / source`，提供 `from_garmin()` 与稳定 ID 生成。
3. `SQLiteMetricStore`：统一 `metrics` 表 + `(kind, source_id)` 唯一约束幂等 upsert + payload 原始 JSON + 同步游标。
4. Garmin 同步适配器：认证封装（SSO + token 缓存 + MFA + 中国区开关）+ 18 类抓取器 + 增量编排。
5. 6 个 Agent 工具：4 个指标查询 + 2 个跨存储对齐。
6. `TimeAligner` 实现：按时间窗桥接客观指标与主观事件。
7. CLI 接入：对话中可查运动数据；同步走独立脚本。

---

## 2. 数据流与边界（重要）

```
Garmin Connect
    │  SSO 登录 / token 缓存 / MFA
    ▼
sport/client.py  ── 认证
    ▼
sport/sync.py    ── 18 类抓取器（可插拔）
    ▼  list[Metric]
SQLiteMetricStore  ── data/metrics.db（客观）
                              │
                              │  TimeAligner（时间窗）
                              ▼
                    SQLiteEventStore ── data/events.db（主观）
```

**铁律**：佳明数据只写 `metrics.db`，绝不写 `events.db`，不生成事件、不做向量化。主观感受仍由对话随口记进 `events.db`。两者物理分库、逻辑分离，只在查询时按时间窗对齐。

---

## 3. 抓取的数据类别（18 类）

| kind | 数据来源方法 | 说明 |
|---|---|---|
| activity | `get_activities_by_date` | 活动：距离/时长/类型/名称，payload 全保留 |
| sleep | `get_sleep_data` | 睡眠 |
| daily | `get_user_summary` | 每日摘要（步数/卡路里/距离等） |
| steps | `get_steps_data` | 步数采样 |
| hrv | `get_hrv_data` | HRV |
| rhr | `get_rhr_day` | 静息心率 |
| heart_rate | `get_heart_rates` | 心率 |
| stress | `get_stress_data` | 压力 |
| body_battery | `get_body_battery` | 身体电量 |
| weight | `get_body_composition` | 体重体脂 |
| respiration | `get_respiration_data` | 呼吸 |
| spo2 | `get_spo2_data` | 血氧 |
| training_readiness | `get_training_readiness` | 训练准备度 |
| training_status | `get_training_status` | 训练状态 |
| vo2max | `get_max_metrics` | VO2Max / 最大摄氧量 |
| hydration | `get_hydration_data` | 水分 |
| intensity | `get_intensity_minutes_data` | 中高强度时长 |
| calories | `get_calories_daily` | 卡路里 |

> 新增类别：在 `sport/sync.py` 的 `FETCHERS` 注册一个 `(client, date) -> list[Metric]` 函数即可，编排逻辑不用改。

---

## 4. 配置

在 `.env`（参考 `.env.example`）：

```ini
GARMIN_EMAIL=你的佳明账号
GARMIN_PASSWORD=你的佳明密码
GARMIN_IS_CN=false        # 中国区账号设 true
GARMIN_TOKEN_DIR=data/garmin_tokens
ASSISTANT_METRICS_DB=metrics.db
```

凭据与 token 缓存目录均已在 `.gitignore` 忽略，**不会提交**。token 命中后免密、自动刷新；仅 refresh token 过期/被撤销时需重新登录（可能要 MFA）。

---

## 5. 使用方法

### 5.1 同步数据（独立脚本）

```bash
# 全量：最近 30 天
.venv/bin/python scripts/sync_garmin.py --days 30

# 指定区间
.venv/bin/python scripts/sync_garmin.py --start 2026-01-01 --end 2026-01-31

# 只同步活动
.venv/bin/python scripts/sync_garmin.py --days 7 --kinds activity

# 增量（从上次游标之后）
.venv/bin/python scripts/sync_garmin.py --incremental

# 试跑（不连接、不落库）
.venv/bin/python scripts/sync_garmin.py --days 7 --dry-run

# 缓解风控：每天之间的间隔秒
.venv/bin/python scripts/sync_garmin.py --days 30 --sleep-between-days 2
```

首次同步会触发登录；如需 MFA，脚本会提示输入验证码。同步报告会列出各类别写入条数与失败明细，单类失败不影响其他类别。

### 5.2 对话查询

```
你：我上周跑得怎么样？
你：最近睡得怎么样？
你：看看我的 HRV 趋势
你：跑10公里那天我感觉怎么样？     ← TimeAligner：指标 → 事件
你：我很累的那几天睡眠如何？        ← TimeAligner：事件 → 指标
你：指标库里现在有什么？            ← metrics_overview
```

---

## 6. Agent 工具

| 工具 | 作用 |
|---|---|
| `query_activities` | 查活动记录（距离/时长/类型） |
| `query_sleep` | 查睡眠 |
| `query_metric` | 按 kind 查任意指标 |
| `metrics_overview` | 指标库概况（条数/分类/时间跨度/游标） |
| `recall_feeling_of_metric` | 指标 → 同时间窗的主观感受 |
| `recall_metric_of_feeling` | 主观感受 → 同时间窗的客观指标 |

---

## 7. 验证结果

- **33 项测试全部通过**（原 20 + 新增 13）。
- 覆盖：Metric 模型/ID、upsert 幂等、区间查询、同步游标、抓取器映射（mock）、同步编排与失败隔离、双向时间对齐、工具注册。
- CLI 端到端：6 个新工具正确注册，指标库初始化正常。
- 同步脚本 `--dry-run` 正常。

**未做真实账号同步**：需要你的 Garmin 凭据。凭据到位后运行 `scripts/sync_garmin.py --days 30` 即可验证真实数据落库。

---

## 8. 风险与已知限制

| 风险 | 说明 / 对策 |
|---|---|
| 非官方库被风控 | Garmin 可能返回 429/403；已做多策略登录 + 缓存 token 自愈 + 每日间隔参数；无 CAPTCHA 自动处理 |
| 国内网络 | `is_cn` 开关区分中国区账号；若连接失败需检查网络/代理 |
| MFA 非交互 | 无交互环境下需先交互登录一次缓存 token |
| 全量历史量大 | 建议先 `--days 30` 试跑，再按需扩大；upsert 幂等，可反复同步 |
| 活动详情未展开 | 当前只存活动摘要（payload 含 Garmin 返回的全部摘要字段）；圈数/轨迹等详情接口调用量大，留作后续按需扩展 |
| 时间对齐窗口 | 默认 ±24h，可配；窗口过大可能引入无关记录 |

---

## 9. 下一步候选

- 配置真实 Garmin 凭据，跑一次真实全量同步并验证数据。
- 活动详情抓取（圈数/分段/轨迹）按需扩展抓取器。
- 阶段 3：Digest 周报（活动 + 睡眠 + 心率趋势聚合）。

# Garmin Personal API 调试报告

> 调试日期：2026-11-23 · 状态：**API 已调通，且已完成整体替换 + 真实同步验证**
> 凭据来源：`.env_garmin`（裸 key，已 gitignore）· 区域：**中国区**
> 官方 Swagger：https://developer.garmin.com/gc-developer-program/personal/swagger-docs/

---

## 1. 结论摘要

用你从官网申请的密钥，**中国区 Garmin Connect Personal API 完全可用**，活动、睡眠、步数、体重、HRV、压力、身体电量等核心运动/健康数据都能拉到真实数据。

这是与之前 `python-garminconnect`（账号密码 + SSO + MFA）**完全不同的官方方案**：无需账号密码、无需两步验证、无需 token 缓存，认证只要两步。**旧账号密码方案已彻底删除，官方 API 已整体替换并完成真实同步验证（见文末第 6 节）。**

---

## 2. 凭据与区域

- 凭据文件：`.env_garmin`，内容是**裸 key 一行**（`GCDC_` 开头，无 `KEY=value` 结构，133 字符）。读取时 `read().strip()` 即可。
- 该 key 是**中国区**密钥，只能用中国区域名；实测国际区返回 404（"key does not exist"）。

| 项 | 值 |
|---|---|
| 中国区 base URL | `https://personal.connectapi.garmin.cn` |
| 国际区 base URL（本 key 不可用） | `https://personal.connectapi.garmin.com` |
| 安全 | key 只从文件读入内存，禁止打印、禁止进 git（`.env_*` 已忽略） |

---

## 3. 认证流程（已验证）

**第一步：key 换 JWT**

```http
POST https://personal.connectapi.garmin.cn/personalapitoken-service/personal-api/personal-api-key/token
Authorization: ApiKey <GCDC key>
```

- 成功：`HTTP 200`，响应体是**裸 JWT 字符串**（不是 JSON，不要 `json.loads`），实测 1108 字符，直接 `.strip()` 使用。
- JWT scope：`CONNECT_READ`、`PERSONAL_API_FITNESS_READ`、`PERSONAL_API_WELLNESS_READ`、`PERSONAL_API_NUTRITION_READ`、`PERSONAL_API_USER_READ`——覆盖全部所需数据。
- 失败：`HTTP 404` = key 不存在/区域不对；`HTTP 400` = key 格式或状态异常。

**第二步：Bearer 调用数据接口**

```http
GET https://personal.connectapi.garmin.cn/<端点路径>
Authorization: Bearer <JWT>
Accept: application/json
```

JWT 有时效。当前验证流程是每次运行重新换 token（一次 POST，开销可忽略）；长期运行可在 token 到期前复用，下一轮实现时可加简单缓存。

---

## 4. 端点实测清单

全部用真实 key + Bearer token 实测（2026-11-23）。`YYYY-MM-DD` 为日期格式。

### 4.1 活动

| 数据 | 端点 | 参数 | 状态 |
|---|---|---|---|
| 活动列表 | `GET /activitylist-service/activities/search/activities` | `start`（偏移）、`limit`（条数） | ✅ 拿到 3 条真实活动 |
| 活动详情 | `GET /activity-service/activity/{activityId}` | 路径参数 | ✅ |
| 活动下载文件 | `GET /download-service/files/activity/{activityId}` | 路径参数 | 未测（FIT 文件，非结构化指标） |
| 活动类型列表 | `GET /activity-service/activity/activityTypes/simple` | — | ❌ 404 |
| 区间活动汇总 | `GET /wellnessactivity-service/activity/summary/range/{from}/{until}` | 路径参数 | 未测 |

### 4.2 健康 / 日常指标

| 数据 | 端点 | 参数 | 状态 |
|---|---|---|---|
| 睡眠 | `GET /sleep-service/sleep/dailySleep` | `date=YYYY-MM-DD` | ✅ |
| 睡眠汇总 | `GET /sleep-service/sleep/dailySleepSummariesByDate` | 未测 | 未测 |
| 体重 | `GET /weight-service/weight/range/{start}/{end}` | 路径参数 | ✅ |
| 步数 | `GET /usersummary-service/stats/steps/daily/{start}/{end}` | 路径参数 | ✅ |
| 压力 | `GET /usersummary-service/stats/stress/daily/{start}/{end}` | 路径参数 | ✅ |
| 身体电量 | `GET /wellness-service/wellness/bodyBattery/reports/daily` | `startDate`、`endDate` | ✅ |
| HRV 单日 | `GET /hrv-service/hrv/{calendarDate}` | 路径参数 | ✅ |
| HRV 范围 | `GET /hrv-service/hrv/daily/{from}/{to}` | 路径参数 | ✅ |
| 每日心率 | `GET /wellness-service/wellness/dailyHeartRate` | `date=YYYY-MM-DD` | ✅ |
| 健身年龄 | `GET /fitnessage-service/fitnessage/v2/{calendarDate}` | 路径参数 | ✅ |
| 每日摘要 | `GET /usersummary-service/v2/dailysummaries` | `startDate`、`endDate` | ✅ |
| VO2Max | `GET /metrics-service/metrics/maxmet/daily/{start}/{end}` | 路径参数 | ❌ 404（key 无权限或路径变更） |
| 每日摘要（旧） | `GET /usersummary-service/usersummary/daily` | `startDate`、`endDate` | ❌ 400（用上面的 v2 替代） |
| 用户设置 | `GET /userprofile-service/userprofile/settings` | — | ✅ |

> 要点：同一类数据常有新旧两套路径，**优先用 v2 / `daily` 路径**，旧的 `usersummary/daily` 会 400。

---

## 5. 关键响应字段（供下一轮字段映射）

### 活动详情 `/activity-service/activity/{id}`
- 顶层：`activityId`、`activityUUID`、`activityName`、`activityTypeDTO`、`eventTypeDTO`、`metadataDTO`、`summaryDTO`、`lapCount` 等。
- `summaryDTO`：`startTimeLocal`、`startTimeGMT`、`startLatitude/Longitude`、`distance`、`duration`、`movingDuration`、`elapsedDuration`、`elevationGain/Loss`、`averageSpeed`、`averageMovingSpeed`、`maxSpeed`、`calories`、`averageHR`、`maxHR`、`minHR`、`averageRunCadence`、`maxRunCadence`、`averageTemperature` 等。

### 活动列表项
`activityId`、`activityName`、`startTimeLocal`、`startTimeGMT`、`activityType`、`eventType`、`distance`、`duration`、`elapsedDuration`、`movingDuration`、`elevationGain/Loss`、`averageSpeed`、`maxSpeed` 等。

### 睡眠 `/sleep-service/sleep/dailySleep`
`calendarDate`、`sleepTimeSeconds`、`napTimeSeconds`、`deepSleepSeconds`、`lightSleepSeconds`、`remSleepSeconds`、`awakeSleepSeconds`、`sleepStart/EndTimestampGMT/Local`、SpO2（average/lowest/highest）、呼吸频率（average/lowest/highest）等。

### 步数单条
`calendarDate`、`totalSteps`、`totalDistance`、`stepGoal`。

### 体重 `/weight-service/weight/range/{start}/{end}`
`dailyWeightSummaries`（近期可能为空）、`totalAverage`（`weight`、`bmi`、`bodyFat`、`bodyWater`、`boneMass`、`muscleMass`、`visceralFat`、`metabolicAge` 等）、`previousDateWeight`、`nextDateWeight`。

### 每日摘要 v2 `/usersummary-service/v2/dailysummaries`
`data`（数组，元素为 `UserDailySummaryV2DTO`）——下一轮需展开一层再映射。

---

## 6. 改造结果（已完成，2026-11-23）

旧账号密码方案已**彻底删除**，官方 API 已整体替换：

1. **已删除**：`sport/` 下的 SSO/账号密码认证与旧抓取器、`garminconnect` 与 `curl_cffi` 依赖、`config.py` 的 `garmin_email/password/is_cn/token_dir` 四个字段、`.env.example` 的对应变量、`.gitignore` 的旧 token 缓存规则、相关测试。全局搜索确认无残留引用。
2. **已实现** `src/assistant/sport/client.py`：轻量官方 API 客户端——从 `.env_garmin` 读裸 key，`POST` 换 JWT（实例内缓存），`GET` 带 Bearer；统一 404/401/403/429/网络异常中文提示；key/JWT 绝不打印，仅用标准库 `urllib`，无新增依赖。
3. **已改造** `src/assistant/sport/sync.py` + `scripts/sync_garmin.py`：**11 类抓取器**（活动、睡眠、每日摘要、步数、HRV、静息心率、心率、压力、身体电量、体重、健身年龄），官方区间端点一次拉一段，映射为 `Metric` 写入 `SQLiteMetricStore`（幂等 upsert、独立 `metrics.db`、不进事件库、不生成事件）；VO2Max 等 404 端点未纳入。
4. **配置**：base URL 固定 `.cn`；新增 `GARMIN_API_KEY_FILE`（默认 `.env_garmin`）；`GARMIN_IS_CN` 已移除。
5. **验证**：用真实中国区 key 同步近 5 天，落库 45 条（11 类），`query_metrics.py` 的 overview / list-kinds / kind 查询全部正常；35 项测试通过。

**日常使用**：
```bash
.venv/bin/python scripts/sync_garmin.py --days 30      # 同步最近 30 天
.venv/bin/python scripts/sync_garmin.py --incremental  # 增量
.venv/bin/python scripts/query_metrics.py --overview   # 查询
```

---

## 7. 已知风险

- `GCDC_` key 有有效期（在官网创建时选择）；过期需去 Garmin Connect 网页重新生成，key 只显示一次。
- 个人 API 有速率限制；当前抓取器按区间批量拉取（请求数已很少），如仍触发 429 可分批同步。
- 官方 API 路径仍在演进（已见旧版 `daily` 接口 400、VO2Max 404）；同步编排已做单类失败隔离，个别端点失效不会中断整体同步。

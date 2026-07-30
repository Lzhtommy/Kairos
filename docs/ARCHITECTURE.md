# Kairos 整体架构规划

> 本文档规划 Kairos 从「纯前端演示」演进为「可上线的 A 股智能选股 SaaS」的完整技术架构。
> 现状：前端已完成，全部数据为 `src/lib/mock-data.ts` 的模拟数据，行情由 `useLiveQuotes` 定时抖动模拟，AI 策略对话为 `setTimeout` 桩。
> 目标：补齐后端、数据、AI、回测与基础设施，形成端到端可运行的系统。

---

## 1. 产品定位与现状盘点

Kairos 是一个 **AI 驱动的 A 股量化选股平台**，核心价值是「用自然语言描述选股逻辑 → AI 生成可回测的策略 → 一键选股与跟踪」。

### 1.1 已实现的前端（现状）

| 模块 | 路由 | 文件 | 现状 |
|---|---|---|---|
| 营销落地页 | `/` | `pages/landing-page.tsx` | 纯静态 |
| 大盘概览 | `/app` | `pages/dashboard-page.tsx` | 指数条 + 行情表，模拟数据 |
| 选股 | `/app/screener` | `pages/screener-page.tsx` | 传统筛选（行业/PE/ROE）+ 策略筛选，前端过滤模拟数据 |
| 策略工坊 | `/app/strategy` | `pages/strategy-builder-page.tsx` | AI 对话 + 代码面板 + 回测预览，全部为桩 |
| 自选股 | `/app/watchlist` | `pages/watchlist-page.tsx` | 取前 6 支模拟数据，星标状态仅本地 `useState` |

技术栈：React 19 + Vite 8 + TypeScript + Tailwind v4 + shadcn/base-ui + react-router 7 + TanStack Table + Phosphor Icons。

### 1.2 现状的关键缺口

- **无真实数据**：行情、基本面、指数全是 `mock-data.ts` 生成；无历史 K 线。
- **无状态持久化**：自选、策略、用户偏好刷新即失。`StarToggle` 的选中态只在组件内。
- **AI 是假的**：`strategy-builder-page.tsx` 里 `send()` 用 `setTimeout` 返回写死的 `GENERATED_CODE`。
- **无账户体系**：侧栏「陆晓岚 / 专业版」是写死的 UI。
- **无回测**：回测指标（+18.4% / -12.7% / 34）是硬编码。
- **部署仅静态**：`deploy/` 显示当前是 GitHub Actions build → rsync 到阿里云 ECS，Nginx 托管纯静态。

---

## 2. 目标架构总览

```mermaid
graph TB
  subgraph Client["客户端"]
    Web["Web 前端<br/>React + Vite (现有)"]
  end

  subgraph Edge["接入层"]
    CDN["CDN / 静态资源"]
    GW["API 网关 / BFF<br/>REST + WebSocket"]
  end

  subgraph Services["后端服务"]
    Auth["认证与用户服务"]
    Market["行情服务<br/>quotes / kline / 指数"]
    Screen["选股服务<br/>筛选引擎"]
    Strat["策略服务<br/>CRUD / 版本"]
    AI["AI 策略引擎<br/>NL → DSL/代码"]
    BT["回测引擎<br/>历史模拟"]
    Alert["告警与通知"]
  end

  subgraph Data["数据层"]
    PG[("PostgreSQL<br/>用户/策略/自选")]
    TS[("时序库<br/>行情/K线")]
    Redis[("Redis<br/>缓存 + 实时 pub/sub")]
    OBJ[("对象存储<br/>回测报告")]
    MQ["消息队列<br/>Kafka / RocketMQ"]
  end

  subgraph Ingest["数据接入"]
    Feed["行情采集器<br/>Tushare / AkShare / 交易所"]
  end

  subgraph External["外部"]
    LLM["LLM<br/>Claude API"]
    Vendor["数据供应商"]
  end

  Web --> CDN
  Web -->|REST/WS| GW
  GW --> Auth & Market & Screen & Strat & AI & Alert
  Strat --> BT
  AI --> LLM
  Market --> TS & Redis
  Screen --> TS & PG
  Strat --> PG
  BT --> TS & OBJ
  Auth --> PG
  Alert --> MQ --> Web
  Feed --> Vendor
  Feed --> TS & MQ
  MQ --> Redis
```

**分层要点**
1. **接入层**：静态资源走 CDN（沿用现有 Nginx/ECS，后续可换 OSS+CDN）；动态请求走 API 网关 / BFF。
2. **服务层**：按领域拆分，初期可作为「模块化单体」部署，流量增长后再拆微服务。
3. **数据层**：关系型（业务）+ 时序（行情）+ Redis（缓存与实时）+ 对象存储 + 消息队列。
4. **数据接入**：独立的采集进程，从供应商拉数据入库并广播增量。

> 上图是**目标形态**。§2.1 给出当前阶段落地的 **MVP 简化形态**——因为行情按「约 1 分钟一次」的低频更新，不追求高频实时，很多为高并发准备的组件可以延后引入。

### 2.1 MVP 简化形态（当前落地决策）

综合两个已确认的决策——**① 数据库单库起步（本地 SQLite → 后续迁 MySQL）；② 行情低频更新（约 1 分钟一次，非高频）**——MVP 阶段可以大幅简化：

```mermaid
graph LR
  Feed["采集脚本<br/>每 ~60s 拉一次<br/>可插拔 DataProvider"] --> DB[("SQLite 单库<br/>业务 + 行情/K线")]
  API["FastAPI<br/>REST"] --> DB
  Web["前端<br/>useLiveQuotes 轮询 60s"] -->|REST| API
```

低频场景下，原目标架构中为「高频」准备的组件**全部延后**：

| 组件 | 目标形态用途（高频） | MVP（~1 分钟）是否需要 |
|---|---|---|
| Kafka / RocketMQ | 行情增量解耦、扇出 | ❌ 不需要，采集脚本直接写库 |
| Redis pub/sub | WebSocket 实时扇出 | ❌ 不需要，前端轮询即可 |
| TimescaleDB / ClickHouse | 海量 tick 写入与聚合 | ❌ 不需要，K 线量小，SQLite 足够 |
| WebSocket 推送 | 双向高频 | ⚠️ 可选，轮询更省事 |
| Redis 缓存 | 挡高频读 | ⚠️ 可选，读压力大时再加 |

**MVP 就是：一个 SQLite 文件（业务数据 + 行情/K 线同库）+ 一个每分钟运行的采集脚本 + FastAPI + 前端 60s 轮询。** 单写锁在「一分钟一次批量写」的节奏下不构成问题（可再开 WAL 模式进一步降低读写冲突）。等真正需要高频、并发或大数据量时，再按上面的目标架构逐组件引入，不影响已写好的业务代码。

---

## 3. 技术选型建议

| 领域 | MVP 选型 | 目标形态 | 理由 / 备选 |
|---|---|---|---|
| 后端主语言 | **Python + FastAPI** | 同左 | 量化生态（pandas/numpy/backtrader/vectorbt）、AI SDK、回测天然契合；备选 Go(高并发行情推送) 或 Node/NestJS(与前端同栈) |
| 关系库 / 业务库 | **SQLite**（单文件） | **MySQL 8**（RDS/PolarDB） | 用 SQLAlchemy + Alembic 隔离方言，`DATABASE_URL` 一行切换即可迁移（迁移注意事项见 §6.4）。MySQL 建库用 `utf8mb4` + InnoDB |
| 行情 / K线存储 | **SQLite 同库** | **TimescaleDB / ClickHouse** | 低频小数据量塞业务库即可；上高频/大数据量再拆专用时序库 |
| 实时推送 | **REST 轮询（60s）** | **WebSocket**（行情）+ SSE（AI 流式） | 1 分钟节奏轮询足够；高频再上 WS。AI 生成始终走 SSE 更简单 |
| 缓存 / 实时扇出 | *(暂不引入)* | **Redis 7** | 最新价缓存、WS 扇出、限流、排行榜（ZSET 做涨幅榜/成交活跃榜） |
| 消息队列 | *(暂不引入)* | **Kafka** / **RocketMQ** | 行情增量、策略命中、告警的异步解耦 |
| 对象存储 | **本地磁盘 / 目录** | **阿里云 OSS** | 回测报告、K线快照、导出文件 |
| AI | **Claude API** | 同左 | 自然语言 → 策略 DSL/代码；结合结构化输出与工具调用 |
| 沙箱 | **RestrictedPython / 子进程限权** | **gVisor / 容器** | 隔离执行 AI 生成的策略代码 |
| 网关 | **Nginx 反代** | **APISIX / Kong** | 复用现有 Nginx，逐步引入 API 网关 |
| 部署 | **单 ECS + Docker Compose** | **阿里云 ACK(K8s)** | 现有单 ECS 起步，容器化后上 K8s |
| 可观测 | **结构化日志** | **OpenTelemetry + Prometheus + Grafana + Loki** | 指标 / 日志 / 链路追踪 |

> **迁移友好是选型的第一原则**：MVP 的简化不能给未来挖坑。数据库靠 ORM + 迁移工具做到无痛切换；数据源靠可插拔 Provider（§8.2）做到换源不改上层；前端靠 `src/api` 抽象做到 mock↔真实零改页面。
>
> 若团队更偏 TypeScript 全栈，可用 **NestJS** 作为 BFF/业务层，把回测与 AI 沙箱这类计算密集部分单独用 Python 微服务承接。本规划以 Python 为主线，并在 §5.7 说明拆分点。

---

## 4. 前端架构演进

现有前端结构良好（`components/ui` 原子组件、`components/market|marketing|strategy` 业务组件、`pages` 页面、`lib` 工具）。演进方向是**把 mock 换成真实数据层**，而非重写。

### 4.1 引入数据访问层

```
src/
  api/                # 新增：API 客户端
    client.ts         # fetch 封装（鉴权头、错误处理、baseURL）
    ws.ts             # WebSocket 客户端（重连、订阅管理）
    quotes.ts         # 行情接口
    strategies.ts     # 策略接口
    screener.ts       # 选股接口
    auth.ts           # 登录/用户
  hooks/              # 由 lib/ 迁出的数据 hooks
    use-live-quotes.ts   # MVP 改为 60s 轮询，接口签名保持不变
    use-strategies.ts
```

- 引入 **TanStack Query** 管理服务端状态（缓存、失效、乐观更新），替代散落的 `useState`。
- `useLiveQuotes` **保持返回签名** `{ stocks, indices }` 不变：MVP 内部从「2.2s 定时抖动」改为「60s 轮询 `/quotes`」，后续升级到「订阅 WS + 增量合并」也不改页面 → 两级平滑迁移。
- 环境变量：`VITE_API_BASE_URL`（MVP）；`VITE_WS_URL` 待上 WS 时再加（`.env.development` / `.env.production`）。
- `mock-data.ts` 保留为 **MSW（Mock Service Worker）** 的数据源，用于本地开发与测试，通过 `VITE_USE_MOCK` 开关切换，不删除既有资产。

### 4.2 认证与鉴权

- 登录页 + Token（JWT/Session）；`api/client.ts` 注入 `Authorization`；401 自动跳登录。
- `AppShell` 侧栏用户信息由 `/me` 接口驱动，替换写死的「陆晓岚」。
- 路由守卫：`/app/*` 需登录。

### 4.3 策略工坊改造

- 对话消息发到后端 `POST /strategies/{id}/chat`，用 **SSE 流式**逐字渲染，替换 `setTimeout` 桩。
- 「运行回测」调 `POST /backtests`，轮询/WS 获取进度，回测预览展示真实指标与净值曲线（新增图表组件，如轻量的 uPlot / Recharts）。

---

## 5. 后端服务设计

初期建议 **模块化单体**（一个 FastAPI 应用，内部按 domain 分包），保留清晰边界，便于后续按需拆分。

### 5.1 认证与用户服务
- 注册/登录（手机号+验证码 / 邮箱+密码 / OAuth），JWT 或 Session。
- 用户档案、会员等级（免费 / 专业版，对应现有「专业版」UI）、配额（策略数、回测次数、AI 调用次数）。
- RBAC / 特性开关。

### 5.2 行情服务
- **实时报价**：`GET /quotes?codes=...`（拉取最新价）；`WS /ws/quotes`（订阅推送）。
- **历史 K 线**：`GET /kline?code=&period=1d&from=&to=`（日线/分钟线）。
- **指数**：`GET /indices`。
- **榜单**：涨幅榜 / 成交活跃榜（Redis ZSET 实时维护，对应 dashboard 的 Tabs）。
- 对应现有类型：`Stock`、`IndexQuote`（`mock-data.ts` 已定义，可直接作为 API 契约基准）。

### 5.3 选股服务（筛选引擎）
- **传统筛选**：行业 / PE 区间 / 最低 ROE 等（现有 `screener-page.tsx` 的条件），下推到时序库/PG 做服务端过滤与分页，替换现有前端 `filter`。
- **策略筛选**：按 `strategyId` 执行策略，返回命中股票 + 命中数（现有 `hitCount`）。
- 支持组合条件、排序、分页；结果可缓存（Redis，按条件哈希做 key）。

### 5.4 策略服务
- 策略 CRUD、版本管理、标签（对应 `Strategy` 类型：`name/description/hitCount/tags/createdAt`）。
- 策略以 **DSL（结构化 JSON）为主、生成代码为辅** 存储（见 §6.2）：DSL 便于安全执行与再编辑，代码用于展示与高级用户微调。
- 定时调度：策略可设为「每日收盘后重算命中」，结果落库并可触发告警。

### 5.5 AI 策略引擎（核心差异化）
```mermaid
sequenceDiagram
  participant U as 用户
  participant FE as 前端(策略工坊)
  participant AI as AI 策略引擎
  participant LLM as Claude API
  participant SB as 沙箱执行器
  participant DB as 策略库

  U->>FE: 自然语言描述选股逻辑
  FE->>AI: POST /strategies/chat (SSE)
  AI->>LLM: 系统提示 + 因子库 schema + 用户描述
  LLM-->>AI: 结构化输出：策略 DSL + 说明 + 代码
  AI->>AI: 校验 DSL(因子白名单/参数范围)
  AI->>SB: 试运行(小样本干跑)
  SB-->>AI: 语法/运行校验通过
  AI-->>FE: 流式返回说明 + 代码 + DSL
  FE->>DB: 保存策略(用户确认后)
```
- **提示工程**：向 LLM 提供**因子白名单**（PE/PB/ROE/换手率/均线/北向资金…，即领域词表）与 DSL schema，用 **结构化输出/工具调用** 让模型产出可机读的 DSL，而非自由代码。
- **安全**：AI 生成的代码/DSL 只能引用白名单因子与受限算子；执行走沙箱（见 §5.6），禁网络/文件/系统调用。
- **可解释**：保留模型的自然语言说明（现有 UI 已有此设计），并对 DSL 做人类可读渲染。

### 5.6 回测引擎
- 输入：策略 DSL + 回测区间 + 调仓频率 + 费率（现有 UI 已展示「2021-01-01 至今 / 每月首个交易日 / 双边 0.05%」）。
- 计算：基于时序库历史数据做向量化回测（vectorbt）或事件驱动（backtrader）。
- 输出指标：年化收益、最大回撤、夏普、胜率、命中数、净值曲线（现有回测预览三指标是子集）。
- **A 股特有规则**：T+1、涨跌停（±10%/±20%/±30%）、ST、停牌、复权（前复权价）、交易日历。
- 异步执行：提交后进队列，Worker 计算，结果与净值曲线存 OBJ + PG，前端轮询/WS 拉进度。

### 5.7 计算密集服务的拆分点
若采用 TS 全栈 BFF，**回测引擎**和 **AI 沙箱执行器**建议独立为 Python 服务（内部 gRPC/HTTP 调用），因其依赖 Python 量化与数据科学生态。

---

## 6. 数据模型

### 6.1 关系型（业务库）核心表

> 字段类型用 SQLAlchemy 抽象表达（`JSON` 而非 PG 专有的 `JSONB`），保证 SQLite 与 MySQL 都能落地。

```sql
users(id, phone, email, password_hash, nickname, tier, created_at)
watchlists(id, user_id, name, is_default)
watchlist_items(watchlist_id, stock_code, added_at)      -- 对应星标自选
strategies(id, user_id, name, description, tags json,
           dsl json, code text, status, created_at, updated_at)
strategy_versions(id, strategy_id, version, dsl, code, created_at)
strategy_runs(id, strategy_id, run_date, hit_count, hit_codes json)
backtests(id, strategy_id, params json, status,
          metrics json, curve_ref, created_at)           -- curve_ref 指向报告存储
chat_messages(id, strategy_id, role, text, code, created_at)
alerts(id, user_id, type, target, condition json, enabled)
```

### 6.2 策略 DSL 示例（结构化、可安全执行、可再编辑）
```json
{
  "universe": { "exclude": ["ST", "停牌"], "market": ["SH", "SZ"] },
  "filters": [
    { "factor": "pe", "op": "lt", "ref": "industry_median" },
    { "factor": "dividend_yield", "op": "gte", "value": 0.03 },
    { "factor": "roe_min_3y", "op": "gte", "value": 0.12 }
  ],
  "rebalance": "monthly_first_trading_day",
  "cost": { "side": "both", "rate": 0.0005 },
  "technical": [
    { "type": "ma_trend", "window": 60, "lookback": 120, "max_down_days": 10, "min_gain_pct": 1.5 },
    { "type": "ma_distance", "fast": 3, "base": 60, "min_pct": -8, "max_pct": 12 },
    { "type": "ma_rising", "windows": [3, 7] },
    { "type": "ma_cross", "fast": 3, "slow": 7, "direction": "golden" }
  ]
}
```
> 与现有 `GENERATED_CODE`（`def screen(stock): ...`）等价，但机器可校验、可回测、可增量编辑。代码视图由 DSL 渲染而来。
>
> `technical` 为 K 线技术形态层（`app/services/technical.py`）：类型白名单 + 参数夹紧，
> 执行时标量 filters 先筛、技术条件只对入围股票取日 K 计算（`strategy_exec.run_dsl`）。
> 当日日 K 由行情采集实时合成（`collect._upsert_today_bars`），保证均线算到最新交易日。

### 6.3 行情 / 时序数据

MVP 阶段这几张表**与业务表同放一个 SQLite 库**；上高频/大数据量后再迁到专用时序库（TimescaleDB / ClickHouse），表结构基本不变。

```
quotes(code, ts, price, prev_close, open, high, low, volume,
       turnover, turnover_rate, pe, pb, market_cap, roe)   -- 每分钟快照，对应 Stock
kline(code, period, ts, open, high, low, close, volume, adj_factor)  -- 走势/spark 与回测
indices(code, ts, price, change, change_pct)              -- 对应 IndexQuote
fundamentals(code, report_date, pe, pb, roe, market_cap)  -- 基本面（低频更新）
```
> 字段直接对齐 `src/lib/mock-data.ts` 的 `Stock` / `IndexQuote`，采集层负责把数据源字段映射到这里。

### 6.4 SQLite → MySQL 迁移注意事项

用 **SQLAlchemy(ORM) + Alembic(迁移)** 从第一天起做数据库无关化，切换只改 `DATABASE_URL`：

```
# MVP
DATABASE_URL=sqlite:///./kairos.db
# 迁移后
DATABASE_URL=mysql+pymysql://user:pass@host/kairos?charset=utf8mb4
```

提前规避两者差异，即可无痛迁移：

| 差异点 | 做法 |
|---|---|
| 外键默认关闭 | SQLite 连接开 `PRAGMA foreign_keys=ON` |
| 并发写 | SQLite 单写锁，开 **WAL 模式**；低频写下不成问题 |
| 布尔 / 时间类型 | 用 SQLAlchemy 的 `Boolean` / `DateTime`，勿存原始 int/text |
| JSON 字段（DSL、tags） | 用 SQLAlchemy `JSON`（MySQL 5.7+ 与 SQLite 均支持），不要用 PG `JSONB` |
| 自增主键 | 交给 ORM，勿手写 `AUTOINCREMENT` / `AUTO_INCREMENT` |
| 字符集 | MySQL 建库 `utf8mb4` + InnoDB（中文 / emoji） |
| `ALTER COLUMN` | SQLite 不支持改列，用 Alembic 的 **batch 模式** |
| 数据搬迁 | 数据量小可用 ORM 逐表 dump/load；量大用 `mysqldump` 前先在 MySQL 建好 schema（Alembic 跑一遍迁移） |

---

## 7. 行情链路

### 7.1 MVP：低频轮询（当前落地）

```mermaid
graph LR
  Source["数据源<br/>DataProvider"] --> Collector["采集脚本<br/>每 ~60s 拉一次 / 归一化"]
  Collector --> DB[("SQLite<br/>quotes / kline / indices")]
  DB --> API["FastAPI /quotes /indices /kline"]
  API -->|REST 轮询 60s| Client["前端<br/>useLiveQuotes"]
```
- 采集脚本独立于 API 进程（`cron` 或 APScheduler 定时），崩溃不影响接口读。
- 结合交易日历与交易时段（9:30–11:30 / 13:00–15:00）控制采集：盘中每分钟拉，盘后停止。
- 前端 `useLiveQuotes(intervalMs)` **接口签名不变**，仅把默认 2200ms 改成 60000ms，内部从「本地抖动」改为「轮询 `/quotes`」，页面零改动。

### 7.2 目标：高频推送（后续演进）

```mermaid
graph LR
  Vendor["数据供应商<br/>(行情源)"] --> Collector["采集器<br/>归一化/校验"]
  Collector --> TS[("时序库")]
  Collector --> MQ["消息队列<br/>行情增量 topic"]
  MQ --> Fanout["推送服务"]
  Fanout --> Redis[("Redis<br/>最新价 + pub/sub")]
  Fanout -->|WebSocket| Client["前端<br/>useLiveQuotes"]
```
- 前端只订阅**自己可见的股票**（当前页/自选/榜单），推送服务按订阅集扇出，降低带宽。
- 前端 `useLiveQuotes` 接口仍不变，内部由 WS 增量驱动，断线自动重连并回补快照——从轮询升级到 WS 同样不改页面。

---

## 8. 数据接入与合规

### 8.1 A 股数据源选型

数据分「实时快照」「历史 K 线」「基本面」三类，稳定性与成本差异很大。

| 数据源 | 类型 | 稳定性 | 成本 | 说明 |
|---|---|---|---|---|
| **Tushare Pro** | 综合（行情+基本面+K线+指数） | ⭐⭐⭐⭐ | 免费注册，高级接口需积分（小额充值） | 官方 API、文档全、字段规范，**长期首选**；实时接口积分门槛偏高 |
| **AkShare** | 综合（封装东财/新浪/交易所） | ⭐⭐⭐ | 完全免费 | 一个库全覆盖，**起步最快**；本质爬取，上游改版偶尔会断，需重试/降级 |
| **baostock** | 历史 K线 + 基本面 | ⭐⭐⭐⭐ | 完全免费 | 免注册、历史数据稳；**仅历史、无实时**，适合回测数据 |
| **东方财富 push2** | 实时快照 | ⭐⭐⭐⭐ | 免费 | 字段最全（价/量/额/换手/PE/市值），1 分钟轮询很稳 |
| **新浪 / 腾讯行情** | 实时快照 | ⭐⭐⭐ | 免费 | 轻量老牌，字段比东财少 |
| **聚宽 / 米筐 / 掘金** | 商业量化数据 | ⭐⭐⭐⭐⭐ | 付费 | 合规、稳定、带回测生态，商业化中期首选 |
| **Wind / 同花顺 iFinD** | 商业终端 | ⭐⭐⭐⭐⭐ | 昂贵（年费万级+） | 机构标准，最权威，MVP 用不上 |

**推荐路径**（对应 §11 路线图）：
1. **MVP**：**AkShare** 一把梭（内部已封装东财/新浪/交易所，实时+历史+基本面全覆盖），采集脚本每分钟拉一次写 SQLite。
2. **求稳**：主力切 **Tushare Pro**（充积分）+ 实时快照走 **东财 push2**，两条腿降低单点风险。
3. **商业化**：上 **聚宽 / 米筐** 等持牌服务商。

字段映射到 App 的 `Stock`：实时价量/换手/PE/市值 ← 东财 push2；pb/roe/历史 K 线 ← Tushare/baostock；指数 ← 东财或 Tushare。

### 8.2 可插拔的 DataProvider 抽象

采集层做成统一接口，换源只改 provider 实现，上层（采集脚本、服务）不动——与「数据库 SQLite→MySQL」同一思路。

```python
class DataProvider(Protocol):
    def get_quotes(self, codes: list[str]) -> list[Quote]: ...      # 实时快照
    def get_kline(self, code: str, period: str,
                  start: date, end: date) -> list[Candle]: ...      # 历史 K 线
    def get_fundamentals(self, codes: list[str]) -> list[Fundamental]: ...
    def get_indices(self) -> list[IndexQuote]: ...
    def trading_calendar(self, year: int) -> list[date]: ...

# 实现：AkShareProvider / TushareProvider / EastmoneyProvider / JoinQuantProvider ...
# 通过配置 PROVIDER=akshare 选择；可组合（实时用东财、历史用 Tushare）
```

- Provider 内部负责把各源字段**归一化**到统一 `Quote/Candle/Fundamental` 模型（即 §6.3 的表结构）。
- 加**重试 + 降级**：主源失败自动切备源（AkShare↔东财），提升稳定性。
- 采集脚本只依赖 `DataProvider` 接口，不感知具体来源。

### 8.3 合规与数据质量

- **行情牌照与免责**：免费接口（东财/新浪/AkShare 爬取）适用于**个人/演示/内部**；Kairos 作为对外分发行情的 SaaS，A 股行情分发需交易所授权（通过持牌服务商如聚宽/米筐/Wind）。页面已有「模拟数据仅供演示」「历史表现不代表未来收益」等文案，正式上线需补充**投资顾问免责、数据来源标注、风险揭示**。
- **备案**：`deploy/README.md` 已注明，绑定大陆域名需 ICP 备案。
- **数据质量**：复权、除权除息、停复牌、退市、代码变更需在 Provider / 采集层统一处理。

---

## 9. 部署与基础设施

### 9.1 现状 → 目标演进
```mermaid
graph TB
  subgraph Now["现状（纯静态）"]
    A1["GitHub Actions build"] --> A2["rsync 到 ECS"] --> A3["Nginx 托管 dist"]
  end
  subgraph MVP["MVP（前后端 + 单库）"]
    M1["前端 dist → Nginx 托管"]
    M2["Nginx 反代 /api → FastAPI(容器)"]
    M3["SQLite 单文件 + 采集脚本(定时)"]
    M1 --- M2 --- M3
  end
  subgraph Target["目标（前后端 + 数据）"]
    B1["前端 dist → OSS + CDN / Nginx"]
    B2["API 网关 / Nginx 反代 → FastAPI(容器)"]
    B3["MySQL(RDS/PolarDB) + Redis + 时序库"]
    B4["采集器 + 回测 Worker(独立容器)"]
    B1 --- B2 --- B3 --- B4
  end
  Now -.演进.-> MVP -.演进.-> Target
```

- **MVP 态**：沿用现有单 ECS，Nginx 同机反代 `/api` 到本地 Docker 的 FastAPI，静态仍由 Nginx 托管（改造 `deploy/nginx-kairos.conf` 增加 `location /api/`）。数据库是同机的 SQLite 文件，采集脚本用 `cron`/APScheduler 定时运行。**注意 SQLite 文件要挂在持久化目录**，别放进会被 rsync `--delete` 清掉的部署目录。
- **成长态**：容器化上 **阿里云 ACK(K8s)**；DB 迁 RDS/PolarDB(MySQL)、加 Redis 云版、时序自建或云托管；前端上 OSS+CDN。
- **环境**：dev / staging / prod 三套；配置走环境变量 + 阿里云 KMS/配置中心管密钥。
- **CI/CD**：现有 `deploy.yml` 扩展为多 job（前端 build+deploy / 后端 build 镜像 + 推 ACR + 滚动发布）；引入 lint、typecheck、测试门禁。

### 9.2 Nginx 反代改造要点（MVP 态）
```
location /api/  { proxy_pass http://127.0.0.1:8000/; }        # 反代 FastAPI
location /      { try_files $uri $uri/ /index.html; }         # 现有 SPA 回退
# /ws/ 待升级到 WebSocket 推送时再加：
# location /ws/ { proxy_pass http://127.0.0.1:8000/; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
```

---

## 10. 横切关注点

- **安全**：HTTPS（现文档提到上 443）、JWT 短时效 + Refresh、限流（网关+Redis）、AI 沙箱隔离、SQL 注入/XSS 防护、密钥不入库入配置中心。
- **可观测性**：OpenTelemetry 埋点，Prometheus 指标（行情延迟、AI 时延、回测耗时），Grafana 看板，Loki 日志，Sentry 前端异常。
- **性能**：行情读多写多用 Redis + 时序聚合；选股结果与 K 线做缓存；回测异步化。
- **测试**：前端 Vitest + Testing Library + MSW；后端 pytest；回测有基准用例（已知策略对已知区间的指标回归）。
- **成本**：MVP 用免费数据源 + 单 ECS + 托管小规格 DB；按用量升级。

---

## 11. 分阶段实施路线图

| 阶段 | 目标 | 关键交付 |
|---|---|---|
| **P0 地基** | 前后端骨架跑通 | FastAPI + SQLAlchemy + Alembic（SQLite）；`src/api` 客户端、TanStack Query、MSW、`.env`、Nginx 反代 |
| **P1 账户 + 自选** | 可登录、自选持久化 | 认证服务、用户/自选表、前端登录页与路由守卫、`/me` 驱动侧栏 |
| **P2 真实行情** | 大盘/自选看真实数据 | 可插拔 DataProvider（AkShare 起步）+ 每分钟采集脚本 → SQLite，`useLiveQuotes` 切 60s 轮询真实源 |
| **P3 选股** | 服务端筛选 | 筛选引擎（传统条件），screener 走后端分页 |
| **P4 策略 + AI** | 自然语言生成策略 | 策略服务 + DSL + Claude 集成 + 沙箱，策略工坊接 SSE |
| **P5 回测** | 真实回测指标与净值曲线 | 回测引擎 + Worker + 报告存储，回测预览接真实数据 |
| **P6 告警 + 调度** | 策略命中/价格告警 | 调度器 + 告警服务 + 通知（站内/邮件/推送） |
| **P7 硬化** | 可观测、容器化、扩容 | ACK 部署、监控告警、限流、压测、合规文案 |

> 每个阶段前端改动最小化：因数据层已抽象在 `src/api`，切换 mock↔真实只影响客户端实现，页面组件不动。

---

## 12. API 契约示例（供前后端并行开发）

```
POST /auth/login            → { token, user }
GET  /me                    → { id, nickname, tier }
GET  /quotes?codes=600519,300750   → Stock[]
GET  /indices               → IndexQuote[]
GET  /kline?code=&period=&from=&to= → Candle[]
WS   /ws/quotes  (订阅 codes，推送增量 Stock)
POST /screener/run          → { total, page, items: Stock[] }   # body: 筛选条件
GET  /strategies            → Strategy[]
POST /strategies            → Strategy
POST /strategies/{id}/chat  (SSE) → 流式 { text, code?, dsl? }
GET  /watchlist             → { items: string[] }
POST /watchlist/{code}      → 加入自选
DELETE /watchlist/{code}    → 移出自选
POST /backtests             → { id }        # 提交回测
GET  /backtests/{id}        → { status, metrics, curveUrl }
```
> `Stock`、`IndexQuote`、`Strategy` 直接复用 `src/lib/mock-data.ts` 中已定义的 TypeScript 类型作为契约基准，前后端共享（可抽到 `packages/shared-types` 或 OpenAPI 生成）。

---

## 13. 小结

- **不重写前端**：现有前端结构清晰，演进核心是引入 `src/api` 数据层并把 `useLiveQuotes` 等 hook 从 mock 切到真实源，页面零改动。
- **后端从模块化单体起步**：Python + FastAPI 承接业务、AI、回测，随流量再拆分；计算密集部分（回测/沙箱）天然适合 Python。
- **MVP 极简、迁移友好**：因行情低频（~1 分钟），MVP 就是「SQLite 单库 + 每分钟采集脚本 + FastAPI + 前端 60s 轮询」，先不上 Redis/MQ/时序库/WS。三条抽象保证未来无痛升级——数据库靠 SQLAlchemy+Alembic（SQLite→MySQL）、数据源靠可插拔 DataProvider（AkShare→Tushare→商业源）、前端靠 `src/api`（mock→轮询→WS）。
- **目标数据分层**：MySQL（业务）+ 时序库（行情）+ Redis（实时/缓存）+ 对象存储（报告）+ MQ（解耦），按需逐步引入。
- **AI 是护城河**：以 DSL 为中心的「自然语言 → 可校验可回测策略」闭环，配合沙箱保证安全。
- **基础设施平滑演进**：从现有单 ECS + Nginx 静态，过渡到 Nginx 反代 + 容器化 FastAPI + SQLite，再到 ACK + MySQL 全托管。

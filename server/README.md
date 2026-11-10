# Kairos 后端 (FastAPI)

A 股智能选股平台的后端。MVP 形态：**SQLite 单库 + 每分钟采集脚本 + FastAPI + 前端 60s 轮询**。

## 特性与降级设计

所有外部依赖都有内置回退，**无需网络 / API Key 即可完整运行**：

| 能力 | 生产实现 | 内置回退（默认） |
|---|---|---|
| 行情数据 | `AkShareProvider`（东财）/ `TencentProvider`（腾讯+新浪，零额外依赖） | `SeedProvider`（确定性合成数据） |
| AI 生成策略 | DeepSeek / 任意 OpenAI 兼容接口（通义、Kimi、Ollama…）/ Claude | 规则解析器（中文关键词 → DSL） |
| 数据库 | MySQL（改 `DATABASE_URL`） | SQLite（默认） |

`PROVIDER=auto` 依次探测 AkShare（东财）→ Tencent（腾讯/新浪），都不可用则回退到 seed；也可用 `PROVIDER=tencent` 强制指定（东财被防火墙/风控拦截的网络推荐，如公司内网）。`TencentProvider` 只依赖核心的 httpx，不需要装 `requirements-data.txt`。`AI_PROVIDER` 选择 LLM：`deepseek`（`auto` 默认，Anthropic API 从大陆服务器无法直连，故以 DeepSeek 为默认）/ `openai`（任意 OpenAI 兼容接口）/ `anthropic`，没配 Key 时回退规则解析器；配置示例见 `.env.example`。

## 本地运行

```bash
cd server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # 核心依赖
# 可选：AkShare 真实数据（需要能访问东财的网络；LLM 调用走 httpx 无需额外包）
# pip install -r requirements-data.txt

cp .env.example .env                      # 按需修改
alembic upgrade head                      # 建表（SQLite/MySQL 通用）
uvicorn app.main:app --reload --port 8000
```

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

启动时会自动 bootstrap 股票池、基本面、历史 K 线，并每 60s 采集一次行情（交易时段内；seed 模式始终刷新）。

## 迁移到 MySQL

```bash
# 1. 建库： CREATE DATABASE kairos CHARACTER SET utf8mb4;
# 2. 改连接串：
export DATABASE_URL="mysql+pymysql://user:pass@host:3306/kairos?charset=utf8mb4"
pip install pymysql
# 3. 跑迁移：
alembic upgrade head
```

已有 SQLite 数据整库搬到空的 MySQL 库（逐表流式复制并核对行数；目标表非空时拒绝写入，加 `--truncate` 覆盖）：

```bash
python -m scripts.migrate_sqlite_to_mysql --source data/kairos.db \
    --target "mysql+pymysql://user:pass@host:3306/kairos?charset=utf8mb4"
# 或把连接串放在环境变量 RDS_DATABASE_URL 里，省略 --target
```

方言差异已在代码里处理：MySQL 下 `Float` 编译为 `DOUBLE`、`DateTime` 为 `DATETIME(6)`；批量写入统一走 `kairos_data.store.upsert_rows`（SQLite `ON CONFLICT` / MySQL `ON DUPLICATE KEY`）。RDS 每次往返约 5ms，**新代码不要写逐行 `db.merge` 或自增主键表的 ORM `add` 循环**，用批量 upsert / Core `insert`。连接串里的密码含特殊字符时按 URL 编码（Alembic 已转义 `%`）。

## Docker

```bash
# 精简镜像（seed / 规则模式）
docker build -t kairos-api ./server
# 生产镜像（含 AkShare 真实数据）
docker build --build-arg WITH_DATA=true -t kairos-api ./server
docker run -p 8000:8000 -v kairos-data:/app/data kairos-api
```

或用仓库根目录的 `docker-compose.yml`（已默认 `WITH_DATA=true`）：`docker compose up -d --build`。

## 目录结构

```
app/
  core/        配置、数据库、鉴权
  models/      SQLAlchemy 模型（用户/行情/策略/自选；数据仓库表映射到 kairos_data.schema）
  providers/   seed 合成数据 + provider 工厂（真实数据源在 kairos_data.sources）
  services/    dsl(校验+执行) / strategy_ai(NL→DSL) / screener / backtest / market / research
  api/         路由：auth / quotes / screener / strategies / watchlist / backtests / sector / research / data
  jobs/        定时任务：行情采集 / 盘后策略 / 板块 / 股票池 / 数据仓库日更 / 公告 / 备份
kairos_data/   独立采集包（可单独 pip 安装）：
  sources/     腾讯 / 东财行情与数据中心 / BaoStock / AkShare / 申万 / 中证官网
  schema.py    股票池、日 K、财报、股东、两融、指数、ETF / 可转债、宏观、公告等表结构
  sync.py      各类数据的同步与全量回补（可断点续跑）
  cli.py       命令行 kairos-data / python -m kairos_data
scripts/       回补与运维脚本（backfill_kline / backfill_delisted / migrate_sqlite_to_mysql …）
alembic/       数据库迁移
```

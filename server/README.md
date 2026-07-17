# Kairos 后端 (FastAPI)

A 股智能选股平台的后端。MVP 形态：**SQLite 单库 + 每分钟采集脚本 + FastAPI + 前端 60s 轮询**。

## 特性与降级设计

所有外部依赖都有内置回退，**无需网络 / API Key 即可完整运行**：

| 能力 | 生产实现 | 内置回退（默认） |
|---|---|---|
| 行情数据 | `AkShareProvider`（东财）/ `TencentProvider`（腾讯+新浪，零额外依赖） | `SeedProvider`（确定性合成数据） |
| AI 生成策略 | DeepSeek API（JSON 输出，国内直连） | 规则解析器（中文关键词 → DSL） |
| 数据库 | MySQL（改 `DATABASE_URL`） | SQLite（默认） |

`PROVIDER=auto` 依次探测 AkShare（东财）→ Tencent（腾讯/新浪），都不可用则回退到 seed；也可用 `PROVIDER=tencent` 强制指定（东财被防火墙/风控拦截的网络推荐，如公司内网）。`TencentProvider` 只依赖核心的 httpx，不需要装 `requirements-data.txt`。`AI_PROVIDER=auto` 有 `DEEPSEEK_API_KEY` 用 DeepSeek，否则用规则解析器（Anthropic API 从大陆服务器无法直连，故选 DeepSeek）。

## 本地运行

```bash
cd server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # 核心依赖
# 可选：AkShare 真实数据（需要能访问东财的网络；AI 用 DeepSeek 走 httpx 无需额外包）
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

代码全程用 SQLAlchemy ORM，无方言特定 SQL；JSON 字段用通用 `JSON` 类型，SQLite/MySQL 均兼容。

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
  models/      SQLAlchemy 模型（用户/行情/策略/自选）
  providers/   DataProvider 抽象 + seed / akshare / tencent 实现 + 工厂
  services/    dsl(校验+执行) / strategy_ai(NL→DSL) / screener / backtest / market
  api/         路由：auth / quotes / screener / strategies / watchlist / backtests
  jobs/        采集脚本
alembic/       数据库迁移
```

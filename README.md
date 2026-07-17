# Kairos · A 股智能选股平台

用自然语言描述选股逻辑，AI 生成可回测的策略，一键选股与跟踪。

- **大盘概览**：实时指数 + 行情表（涨幅榜 / 成交活跃 / 自选）
- **选股**：传统指标筛选（行业 / PE / ROE）+ 按已保存策略筛选
- **策略工坊**：自然语言 → AI 生成策略 DSL/代码（流式）→ 保存 → 回测（年化/回撤/夏普/胜率 + 净值曲线）
- **自选**：星标持久化
- **账户**：注册 / 登录，数据按用户隔离

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19 · Vite · TypeScript · Tailwind v4 · shadcn/base-ui · TanStack Query/Table · react-router |
| 后端 | Python · FastAPI · SQLAlchemy · Alembic |
| 数据库 | SQLite（MVP）→ MySQL（改 `DATABASE_URL` 即迁移） |
| 数据源 | AkShare（真实）/ 内置合成数据（回退），可插拔 `DataProvider` |
| AI | Claude API / 内置规则解析器（无 Key 也能用） |
| 部署 | 阿里云 ECS · Nginx（静态 + `/api` 反代）· Docker Compose |

> MVP 采用「SQLite 单库 + 每分钟采集 + 60s 轮询」的低频形态，Redis/MQ/时序库/WebSocket 按需在后续引入。设计与任务拆解见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) 与 [`docs/MVP-TASKS.md`](./docs/MVP-TASKS.md)。

## 本地开发

**后端**（终端 1）：
```bash
cd server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**前端**（终端 2）：
```bash
npm install
npm run dev          # http://localhost:5173 （/api 自动代理到 :8000）
```

打开 http://localhost:5173 → 注册一个账号即可体验全部功能。无需任何外部 API Key 或网络：
后端默认用内置合成行情与规则解析器；配置了 `ANTHROPIC_API_KEY` / 可访问 AkShare 时自动切换到真实 AI / 数据。

详见 [`server/README.md`](./server/README.md)（含 MySQL 迁移、Docker）与 [`deploy/README.md`](./deploy/README.md)（ECS 部署）。

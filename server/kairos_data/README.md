# kairos-data

把 A 股公开数据抓到你自己的数据库里，增量更新、可直接用 SQL 查询。是 [Kairos](https://github.com/Lzhtommy/Kairos) 的数据层。

**不附带任何数据**：所有数据都在运行时从公开接口抓取，存到你本地或你自己的数据库。

## 覆盖的数据

| 表 | 内容 | 来源 |
|---|---|---|
| `stock_info` | 沪深 A 股股票池，含已退市股票与上市 / 退市日期 | BaoStock |
| `kline` | 日 K（前复权，成交量单位股），在市股票补到上市日，退市股全历史 | 腾讯，限流时 BaoStock |
| `daily_basic` | 每日指标：ST 标记、停牌、换手率、成交额、PE(TTM) / PB(MRQ) / PS(TTM) / 市现率、总市值、不复权收盘与前收（沪深，补到上市日） | BaoStock |
| `fin_report` | 定期报告主要指标，按报告期，带公告日（1988 年起） | 东方财富数据中心 |
| `fin_statement` | 资产负债表 / 利润表 / 现金流量表主要科目（各 13~15 项） | 东方财富数据中心 |
| `dividend` | 分红送转：预案 / 登记 / 除权日，每 10 股派现、送股、转增 | 东方财富数据中心 |
| `share_capital` | 股本变动：总股本、流通 A 股、限售股、变动原因 | 东方财富数据中心 |
| `holder_count` | 股东户数（数据源实际从 2013 年起） | 东方财富数据中心 |
| `top_holder` | 十大股东、十大流通股东（按报告期） | 东方财富数据中心 |
| `lockup` | 限售解禁（历史与未来计划） | 东方财富数据中心 |
| `margin_trading` | 融资融券个股明细（2010 年起） | 东方财富数据中心 |
| `billboard` / `block_trade` | 龙虎榜（2004 年起）、大宗交易（2000 年起） | 东方财富数据中心 |
| `index_bar` | 11 个主要指数日线（上证、深成、创业板、50 / 300 / 500 / 1000 / 2000、科创 50、北证 50、中证全指） | 东方财富 |
| `index_constituent` | 50 / 300 / 500 历次调整后的成分（2005 年起）；中证系列当期成分与权重（月度快照） | BaoStock、中证指数官网 |
| `security_info` / `security_bar` | ETF、可转债清单（可转债带正股、评级、规模、转股价）与日线 | BaoStock、东方财富 |
| `macro_series` | GDP、CPI、PPI、PMI、工业增加值、固投、进出口、M1/M2、新增贷款、社零、外储、LPR、Shibor、存准率、中美国债收益率、两融余额 | 东方财富数据中心 |
| `announcement` | 上市公司公告列表 | 东方财富公告中心 |

财报、分红、公告都带公告日，可以按"当时市场已经知道什么"来查。

## 约定

- **单位**：金额一律为元，成交量和股本一律为股（数据源给的"手"在写入时已 ×100）；比率类字段（换手率、同比、ROE、溢价率等）为百分数数值，如 3.5 表示 3.5%。
- **日期**：日线表的交易日都是 `DATE`。`kline` 的列名沿用 `ts`，其余表叫 `trade_date`；报告期叫 `report_date`，公告日叫 `notice_date`。
- **代码**：6 位数字，不带交易所后缀，交易所见 `stock_info.market`（SH / SZ / BJ）。指数代码会和股票代码重复（如上证指数与平安银行都是 000001），所以指数单独存放在 `index_bar` / `index_constituent` 的 `index_code` 列，不要和股票放进同一张表。
- **主键**：日线和明细表都用业务主键，例如 `(code, period, ts)`、`(code, trade_date)`，库层防止重复。大宗交易同一天可能出现价量完全相同的多笔，所以用 `(code, trade_date, seq)`，`seq` 是同股同日内的序号。

## 安装与使用

```bash
pip install "kairos-data[baostock,csindex]"

kairos-data all          # 建表 + 股票池 + 财报 / 分红 / 宏观 / 指数成分全量 + 全部日 K + 每日指标
kairos-data daily        # 之后每个交易日跑一次增量
kairos-data announcements
```

默认写入当前目录的 `kairos_data.db`（SQLite）。写 MySQL：

```bash
pip install "kairos-data[all]"
kairos-data all --db "mysql+pymysql://user:pass@host:3306/stock?charset=utf8mb4"
```

全量回补需要几个小时（十大股东、两融、日 K、每日指标最慢）。所有命令都是幂等的；`backfill` 会跳过已有的报告期 / 交易日，中断后重跑即可续上。
在 Python 里使用：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from kairos_data import schema, sync

engine = create_engine("sqlite:///kairos_data.db")
schema.metadata.create_all(engine)
with Session(engine) as db:
    sync.refresh_listing(db)
    sync.daily(db)
```

## 声明

- 仅供个人学习与研究使用，不构成任何投资建议。
- 数据的权利归原始提供方（交易所、东方财富、BaoStock 等）所有。使用者应自行遵守各数据源的服务条款，**不得将抓取的数据用于再分发或商业用途**。
- 工具默认限速，请勿修改为高频请求，避免对数据源造成压力。
- 数据可能存在缺失或错误，作者不对数据的准确性、完整性负责。

## License

MIT

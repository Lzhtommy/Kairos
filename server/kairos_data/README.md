# kairos-data

把 A 股公开数据抓到你自己的数据库里，增量更新、可直接用 SQL 查询。是 [Kairos](https://github.com/Lzhtommy/Kairos) 的数据层。

**不附带任何数据**：所有数据都在运行时从公开接口抓取，存到你本地或你自己的数据库。

## 覆盖的数据

| 表 | 内容 | 来源 |
|---|---|---|
| `stock_info` | 沪深 A 股股票池，含已退市股票与上市 / 退市日期 | BaoStock |
| `kline` | 日 K（前复权，成交量单位：手），在市股票补到上市日，退市股全历史 | 腾讯，限流时 BaoStock |
| `fin_report` | 定期报告主要指标，按报告期，带公告日（1988 年起） | 东方财富数据中心 |
| `dividend` | 分红送转：预案 / 登记 / 除权日，每 10 股派现、送股、转增 | 东方财富数据中心 |
| `index_constituent` | 上证 50、沪深 300、中证 500 历次调整后的成分（2005 年起） | BaoStock |
| `macro_series` | GDP、CPI、PPI、PMI、M1/M2、社零、LPR、Shibor、中美国债收益率 | 东方财富数据中心 |
| `announcement` | 上市公司公告列表 | 东方财富公告中心 |

财报、分红、公告都带公告日，可以按"当时市场已经知道什么"来查。

## 安装与使用

```bash
pip install "kairos-data[baostock]"

kairos-data all          # 建表 + 股票池 + 财报 / 分红 / 宏观 / 指数成分全量 + 全部日 K
kairos-data daily        # 之后每个交易日跑一次增量
kairos-data announcements
```

默认写入当前目录的 `kairos_data.db`（SQLite）。写 MySQL：

```bash
pip install "kairos-data[all]"
kairos-data all --db "mysql+pymysql://user:pass@host:3306/stock?charset=utf8mb4"
```

财报等全量回补约 15~45 分钟；全市场日 K 补到上市日要几个小时。所有命令都是幂等的，中断后重跑即可。

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

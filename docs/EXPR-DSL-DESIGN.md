# 表达式条件层（expr）设计

> 目标：把技术条件从「枚举白名单指标」演进为「基础算子自由组合」，让 AI 能表达长尾指标需求
> （振幅、量比、低开高走、N 日新高回撤……），而计算仍由引擎确定性执行——可回测、可复现、零推理成本。
> 定位：`technical` 层新增一种条件类型 `expr`，与现有白名单类型并存，不替代它们。

---

## 1. 动机与原则

现状链路是「自然语言 → DeepSeek 生成受限 DSL → 后端解释器算数」。每种指标是 technical.py 里
一个手写分支（`SPECS` 白名单），新指标必须改代码。但绝大多数指标需求都能由**原始日 K 序列 +
少量滚动算子**组合出来。

三条不变的原则（与现有 DSL 一致）：

1. **LLM 只翻译，不算数**——计算必须确定性、可在历史任意一天复算（回测口径一致性）。
2. **不 eval 任何代码**——公式解析为 AST 后按节点/函数白名单校验，自研求值器执行。
3. **数据不足即不通过**——窗口不够、除零、缺值一律判 False，不降级凑数。

## 2. DSL 形态

`technical` 数组里新增一种条件：

```json
{
  "type": "expr",
  "formula": "(max(high, 5) - min(low, 5)) / shift(close, 5) * 100 >= 15",
  "desc": "近5日振幅不低于15%"
}
```

- `formula`：布尔表达式，逐日求值，为 True 的交易日即信号日（与其他 technical 条件 AND）。
- `desc`：AI 生成的中文一句话描述，仅用于前端展示与策略摘要（`_tech_desc` 直接返回它）。
- 与白名单类型并存：常用条件（`ma_cross`、`rsi_range`…）仍走白名单——更省 token、参数有
  边界夹持、不易写错；`expr` 兜长尾。提示词里明确这个优先级。

## 3. 语法

采用 **Python 表达式子集**，用标准库 `ast.parse(mode="eval")` 解析（只解析、不编译执行），
好处是零成本获得健壮的解析器，且 LLM 对 Python 语法出错率最低。

允许的语法节点（白名单，其余一律拒绝）：

| 类别 | 节点 | 示例 |
|---|---|---|
| 四则运算 | `+ - * /` | `(high - low) / close` |
| 取负/取正 | `UnaryOp` | `-3` |
| 比较 | `> >= < <= == !=`，支持链式 | `-2 <= x <= 5` |
| 布尔 | `and` `or` `not` | `a > 0 and b < 1` |
| 函数调用 | 仅白名单函数，不允许方法/属性 | `ma(close, 20)` |
| 变量 | 仅白名单序列名 | `close` |
| 字面量 | int / float | `1.05` |

明确禁止：下标、属性访问、字符串、字典/列表、lambda、条件表达式、`**`（防指数爆炸）、
一切名字不在白名单内的标识符。

## 4. 变量与函数

### 4.1 变量（对齐的日 K 序列，升序）

| 名字 | 含义 |
|---|---|
| `open` `high` `low` `close` | 当日开/高/低/收 |
| `volume` | 当日成交量（手） |

刻意最小化：`prev_close` 等派生量用 `shift(close, 1)` 组合即可，不单独开洞。

### 4.2 函数（第一期）

滚动窗口 `n` 一律**含当日**（与 `cum_change` 新口径的"窗口含今日"一致），`n` 必须是
**整数字面量**（不允许 `ma(close, x+1)`），取值 1..240——这样才能静态推导取数窗口（见 §6）。

| 函数 | 语义 | 返回 |
|---|---|---|
| `shift(x, n)` | n 个交易日前的值 | 序列 |
| `ma(x, n)` | n 日简单均值 | 序列 |
| `ema(x, n)` | n 日指数均值（复用现有 `_ema_full`） | 序列 |
| `sum(x, n)` | n 日滚动求和 | 序列 |
| `max(x, n)` / `min(x, n)` | n 日滚动最高/最低 | 序列 |
| `std(x, n)` | n 日滚动标准差 | 序列 |
| `abs(x)` | 绝对值 | 序列 |
| `pct(a, b)` | `(a / b - 1) * 100`，b 为 0/缺值 → None | 序列 |
| `count(cond, n)` | 近 n 日 cond 成立的天数 | 序列 |
| `all(cond, n)` / `any(cond, n)` | 近 n 日 cond 全部/至少一天成立 | 布尔序列 |
| `cross_up(a, b)` / `cross_down(a, b)` | a 上穿/下穿 b 的首日（昨 ≤ 今 > 语义同 `ma_cross`） | 布尔序列 |

第二期候选（先不做）：`rsi(x, n)`、`slope(x, n)`、`rank`/截面算子、基本面序列（PE 历史）。

### 4.3 缺值语义

- 窗口不足（序列开头）、除零 → 该日值为 None。
- None 参与任何运算/比较 → 结果 None。
- 顶层结果为 None 的交易日 → 条件判 False。

## 5. 校验（validate_dsl 扩展）

保存策略时一次性校验，执行时再校验一遍（纵深防御，公式以文本存库）：

1. `ast.parse(formula, mode="eval")`，语法错误直接报 `DSLError`。
2. 遍历 AST：节点类型白名单、函数名白名单、变量名白名单、窗口参数为 1..240 的整数字面量。
3. 资源上限：公式长度 ≤ 300 字符、AST 节点数 ≤ 100、嵌套深度 ≤ 10、
   推导出的取数窗口 ≤ 250（`_MAX_BARS`）。
4. 类型检查：顶层必须是比较或布尔运算（结果是布尔序列），比较两侧必须是数值序列/标面量——
   在 AST 上做一遍简单类型推导即可，防止 `close and volume` 这类无意义写法。

## 6. 取数窗口推导（bars_needed）

递归推导每个节点需要的历史长度：

```
need(变量/字面量)      = 1
need(shift(x, n))     = need(x) + n
need(ma/sum/max/…(x,n)) = need(x) + n - 1
need(cross_up(a, b))  = max(need(a), need(b)) + 1
need(二元运算/比较)     = max(两侧 need)
```

`bars_needed` 对 expr 条件返回该值，与其他条件取 max，封顶 250。这是把窗口参数限制为
字面量换来的能力——取数量在保存时就静态可知。

## 7. 求值器与集成

### 7.1 求值器

`technical.py` 新增 ~200 行：AST → 递归求值，输入 OHLCV 序列 dict，输出与 `signal_series`
其他分支一样的 `list[bool]`。全部逐日数组操作，O(节点数 × n)，n ≤ 250；滚动 max/min 用
单调队列、sum/ma 用前缀和，其余 naive 即可。标量池已经把股票数预筛到小集合，性能不是问题。

### 7.2 数据管道改造（本设计里唯一的破坏性改动）

`signal_series` 目前只接 `closes/volumes/opens` 三个平行参数，expr 需要 high/low。
借此收拢成一个结构：

```python
@dataclass
class Bars:
    open: list[float]; high: list[float]; low: list[float]
    close: list[float]; volume: list[int]

signal_series(technical, bars: Bars) -> list[bool]
passes(technical, bars: Bars) -> bool
```

- `series_by_code` 补取 high/low，返回 `dict[code, Bars]`（上次加 opens 时已经证明这里
  每加一列就改一次签名不可持续）。
- backtest 的 `series` 本来就有全部五列，组装 Bars 零成本。
- 现有各白名单分支改为从 `bars.close`/`bars.volume` 取数，语义零变化，回归测试保护。

### 7.3 AI 提示词

`_spec_doc` 增加一节：语法、变量、函数表、缺值语义 + 5 个 few-shot（见 §8），并写明策略：
**能用白名单类型就用白名单类型，expr 只用于白名单表达不了的条件**；`desc` 必填。
`rule_based` 兜底解析器不生成 expr（只有 LLM 主路径产出）。

### 7.4 前端

无需改动：`expr` 条件随 DSL 透传，展示用 `desc`。

## 8. 示例（也是提示词 few-shot）

| 需求 | formula |
|---|---|
| 近 5 日振幅 ≥ 15% | `(max(high, 5) - min(low, 5)) / shift(close, 5) * 100 >= 15` |
| 低开高走（低开 2% 以上、收红） | `pct(open, shift(close, 1)) <= -2 and close > open` |
| 量比 > 2（当日量 / 5 日均量） | `volume / ma(volume, 5) > 2` |
| 距 60 日新高回撤不足 5% | `pct(close, max(high, 60)) >= -5` |
| 连续 3 日缩量 | `all(volume < shift(volume, 1), 3)` |
| 站上所有均线（5/10/20/60） | `close > ma(close, 5) and close > ma(close, 10) and close > ma(close, 20) and close > ma(close, 60)` |
| 5 日线上穿 20 日线且当日放量 | `cross_up(ma(close, 5), ma(close, 20)) and volume > ma(volume, 20) * 1.5` |

## 9. 实施拆分

| 阶段 | 内容 | 交付物 |
|---|---|---|
| P1 | 求值器 + 校验器 + bars_needed 推导，纯函数层 | `expr.py` + 单测（语法拒绝、缺值、逐日语义、窗口推导） |
| P2 | Bars 重构 + 接入 `signal_series`/`passes`/`series_by_code`/backtest | 现有 54+ 用例全绿 + expr 端到端用例 |
| P3 | 提示词 + `_tech_desc` + few-shot 验证 | 自然语言 → expr 的生成质量抽查 |

P1/P2 可合并为一个 PR；P3 独立，便于单独调提示词。

## 10. 不做什么（Non-goals）

- **截面表达式**（"振幅排全市场前 10%"）：涉及跨股票对齐，留给现有 `cross` 算子或二期。
- **基本面历史序列**（PE 走势）：快照表才有历史，粒度和复权口径都是另一个问题。
- **LLM 生成 Python 进沙箱**：自由度收益对单机自用系统不抵沙箱逃逸/死循环/审计成本。
- **分钟级数据**：整个系统目前是日 K 口径。

## 11. 已考虑的替代方案

| 方案 | 否决原因 |
|---|---|
| LLM 查询时直接推理指标值 | 不可复现、算术不可靠、回测无法逐日重算、成本高 |
| 自定义语法（非 Python 子集） | 要自写 parser，LLM 生成错误率更高，无收益 |
| `numexpr`/`pandas.eval` | 引入依赖且白名单控制力弱，`ast` 白名单更贴合现有安全模型 |
| 每个新指标继续加白名单类型 | 现状路径，长尾需求下每次都要改代码发版 |

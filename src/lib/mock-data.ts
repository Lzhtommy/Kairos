export type Stock = {
  code: string
  name: string
  market: "SH" | "SZ"
  industry: string
  price: number
  prevClose: number
  open: number
  high: number
  low: number
  volume: number // 手
  turnover: number // 亿元
  turnoverRate: number // %
  pe: number | null
  pb: number
  marketCap: number // 亿元
  roe: number
  spark: number[]
}

export const INDUSTRIES = [
  "白酒", "银行", "新能源", "半导体", "医药", "军工", "汽车", "家电", "食品饮料", "券商",
] as const

function seededRandom(seed: number) {
  // mulberry32：低碰撞，避免不同股票产生相同的数值序列
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function hashCode(str: string) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function genSpark(rand: () => number, base: number, points = 20) {
  const arr: number[] = []
  let v = base
  for (let i = 0; i < points; i++) {
    v += (rand() - 0.5) * base * 0.015
    arr.push(Number(v.toFixed(2)))
  }
  return arr
}

const RAW: Array<[string, string, "SH" | "SZ", string, number, number]> = [
  ["600519", "贵州茅台", "SH", "白酒", 1682.0, 21100],
  ["300750", "宁德时代", "SZ", "新能源", 187.35, 8230],
  ["601318", "中国平安", "SH", "银行", 47.82, 8760],
  ["000858", "五粮液", "SZ", "白酒", 128.6, 4990],
  ["600036", "招商银行", "SH", "银行", 34.5, 8690],
  ["002594", "比亚迪", "SZ", "汽车", 251.8, 7330],
  ["601899", "紫金矿业", "SH", "军工", 16.42, 4310],
  ["688981", "中芯国际", "SH", "半导体", 82.6, 6510],
  ["000333", "美的集团", "SZ", "家电", 68.9, 4820],
  ["600809", "山西汾酒", "SH", "白酒", 178.4, 2180],
  ["300760", "迈瑞医疗", "SZ", "医药", 226.5, 2740],
  ["601888", "中国中免", "SH", "食品饮料", 62.3, 1290],
  ["000725", "京东方A", "SZ", "半导体", 4.12, 1420],
  ["600030", "中信证券", "SH", "券商", 24.6, 1860],
  ["002415", "海康威视", "SZ", "半导体", 29.8, 2790],
  ["300059", "东方财富", "SZ", "券商", 15.3, 2380],
  ["601012", "隆基绿能", "SH", "新能源", 18.9, 1430],
  ["600276", "恒瑞医药", "SH", "医药", 45.7, 2920],
  ["002230", "科大讯飞", "SZ", "半导体", 42.1, 970],
  ["601166", "兴业银行", "SH", "银行", 17.8, 3690],
]

export const STOCKS: Stock[] = RAW.map(([code, name, market, industry, price, marketCap], i) => {
  const rand = seededRandom(hashCode(code) + i)
  const changePct = (rand() - 0.48) * 6
  const prevClose = Number((price / (1 + changePct / 100)).toFixed(2))
  const high = Number((price * (1 + rand() * 0.015)).toFixed(2))
  const low = Number((price * (1 - rand() * 0.015)).toFixed(2))
  return {
    code,
    name,
    market,
    industry,
    price,
    prevClose,
    open: Number((prevClose * (1 + (rand() - 0.5) * 0.01)).toFixed(2)),
    high,
    low,
    volume: Math.round(rand() * 800000 + 20000),
    turnover: Number((rand() * 40 + 0.5).toFixed(2)),
    turnoverRate: Number((rand() * 4 + 0.1).toFixed(2)),
    pe: rand() > 0.15 ? Number((rand() * 55 + 8).toFixed(1)) : null,
    pb: Number((rand() * 8 + 0.8).toFixed(2)),
    marketCap,
    roe: Number((rand() * 28 + 2).toFixed(1)),
    spark: genSpark(rand, price),
  }
})

export type IndexQuote = {
  code: string
  name: string
  price: number
  change: number
  changePct: number
}

export const INDICES: IndexQuote[] = [
  { code: "000001", name: "上证指数", price: 3187.42, change: 12.86, changePct: 0.41 },
  { code: "399001", name: "深证成指", price: 10203.71, change: -34.52, changePct: -0.34 },
  { code: "399006", name: "创业板指", price: 2094.33, change: 18.07, changePct: 0.87 },
  { code: "000688", name: "科创50", price: 1012.55, change: -6.21, changePct: -0.61 },
  { code: "000300", name: "沪深300", price: 3821.09, change: 9.44, changePct: 0.25 },
]

export type Strategy = {
  id: string
  name: string
  description: string
  hitCount: number
  createdAt: string
  tags: string[]
}

export const STRATEGIES: Strategy[] = [
  {
    id: "st-1",
    name: "低估值高股息",
    description: "PE 低于行业中位数，股息率高于 3%，且近三年 ROE 稳定在 12% 以上",
    hitCount: 34,
    createdAt: "2026-06-02",
    tags: ["价值", "股息"],
  },
  {
    id: "st-2",
    name: "放量突破年线",
    description: "股价上穿250日均线，成交量较20日均量放大1.8倍以上，换手率低于8%",
    hitCount: 12,
    createdAt: "2026-06-18",
    tags: ["趋势", "量价"],
  },
  {
    id: "st-3",
    name: "北向资金连续加仓",
    description: "陆股通持股比例连续5个交易日提升，且股价站上20日均线",
    hitCount: 21,
    createdAt: "2026-07-01",
    tags: ["资金流", "外资"],
  },
]

export function formatCompact(n: number, unit = "") {
  return `${n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${unit}`
}

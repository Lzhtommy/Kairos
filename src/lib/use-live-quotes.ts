import { useEffect, useRef, useState } from "react"
import { STOCKS, INDICES, type Stock, type IndexQuote } from "@/lib/mock-data"

function jitter<T extends Stock>(stock: T): T {
  const drift = (Math.random() - 0.5) * stock.price * 0.004
  const price = Number((stock.price + drift).toFixed(2))
  return {
    ...stock,
    price,
    high: Math.max(stock.high, price),
    low: Math.min(stock.low, price),
    spark: [...stock.spark.slice(1), price],
  }
}

function jitterIndex(idx: IndexQuote): IndexQuote {
  const drift = (Math.random() - 0.5) * idx.price * 0.0015
  const price = Number((idx.price + drift).toFixed(2))
  const change = Number((idx.change + drift).toFixed(2))
  const changePct = Number(((change / (price - change)) * 100).toFixed(2))
  return { ...idx, price, change, changePct }
}

export function useLiveQuotes(intervalMs = 2200) {
  const [stocks, setStocks] = useState<Stock[]>(STOCKS)
  const [indices, setIndices] = useState<IndexQuote[]>(INDICES)
  const tick = useRef(0)

  useEffect(() => {
    const id = setInterval(() => {
      tick.current += 1
      setStocks((prev) => prev.map((s) => (Math.random() > 0.35 ? jitter(s) : s)))
      setIndices((prev) => prev.map((i) => (Math.random() > 0.3 ? jitterIndex(i) : i)))
    }, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])

  return { stocks, indices }
}

export function changePct(stock: Stock) {
  return ((stock.price - stock.prevClose) / stock.prevClose) * 100
}

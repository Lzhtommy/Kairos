import { Fragment } from "react"

const KEYWORDS = new Set([
  "def", "return", "if", "elif", "else", "for", "in", "import", "from", "as",
  "and", "or", "not", "True", "False", "None", "class", "while", "break", "continue",
])

const TOKEN_RE = /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\b\d+\.?\d*\b)|(\b[A-Za-z_][A-Za-z0-9_]*\b)|([()[\]{}.,:=<>+\-*/%]+)/gm

function highlight(code: string) {
  const nodes: React.ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  let key = 0
  TOKEN_RE.lastIndex = 0
  while ((match = TOKEN_RE.exec(code))) {
    if (match.index > last) nodes.push(<Fragment key={key++}>{code.slice(last, match.index)}</Fragment>)
    const [full, comment, str, num, word] = match
    if (comment) {
      nodes.push(<span key={key++} className="text-muted-foreground/70">{comment}</span>)
    } else if (str) {
      nodes.push(<span key={key++} className="text-down">{str}</span>)
    } else if (num) {
      nodes.push(<span key={key++} className="text-chart-2">{num}</span>)
    } else if (word && KEYWORDS.has(word)) {
      nodes.push(<span key={key++} className="text-primary font-medium">{word}</span>)
    } else {
      nodes.push(<Fragment key={key++}>{full}</Fragment>)
    }
    last = match.index + full.length
  }
  if (last < code.length) nodes.push(<Fragment key={key++}>{code.slice(last)}</Fragment>)
  return nodes
}

export function CodeBlock({ code, className }: { code: string; className?: string }) {
  const lines = code.split("\n")
  return (
    <pre className={className}>
      <code className="grid font-mono text-[12.5px] leading-[1.7]">
        {lines.map((line, i) => (
          <span key={i} className="grid grid-cols-[2.5ch_1fr] gap-3">
            <span className="select-none text-right text-muted-foreground/50">{i + 1}</span>
            <span className="whitespace-pre text-foreground/90">{highlight(line)}</span>
          </span>
        ))}
      </code>
    </pre>
  )
}

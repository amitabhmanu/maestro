"""Read HTML tables (Wikipedia's `wikitable`s in particular) into rows of cells.

Row and column spans are expanded, so every row has one cell per column, and each table remembers
the section heading above it. A cell keeps its text, the links in it and whether it was bold
(Wikipedia marks award winners and chart toppers in bold). Standard library only.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Cell:
    text: str = ""
    links: list[str] = field(default_factory=list)
    bold: bool = False
    header: bool = False


@dataclass
class Table:
    heading: str
    headings: list[str]  # every heading above the table, outermost first (h2, h3, ...)
    classes: list[str]
    rows: list[list[Cell]]

    def header_texts(self) -> list[str]:
        """Text of the leading header rows joined per column ("Peak chart positions US Country")."""
        heads: list[list[str]] = []
        for row in self.rows:
            if not all(c.header for c in row):
                break
            heads.append([c.text for c in row])
        if not heads:
            return []
        width = max(len(r) for r in heads)
        out = []
        for i in range(width):
            parts = []
            for r in heads:
                if i < len(r) and r[i] and (not parts or parts[-1] != r[i]):
                    parts.append(r[i])
            out.append(" ".join(parts))
        return out

    def body(self) -> list[list[Cell]]:
        return [r for r in self.rows if not all(c.header for c in r)]


def clean(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"\[\s*(?:\d+|[a-z]|note \d+|citation needed)\s*\]", "", text)  # footnote marks
    return re.sub(r"\s+", " ", text).strip()


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self.stack: list[dict] = []  # open tables (nested tables are read on their own)
        self.heads: dict[int, str] = {}
        self.in_head: int | None = None
        self.head_text: list[str] = []
        self.skip = 0  # inside <style>, <script> or <sup class=reference>
        self.bold = 0

    # headings ------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script") or (tag == "sup" and "reference" in (a.get("class") or "")):
            self.skip += 1
            return
        if self.skip:
            return
        if re.fullmatch(r"h[2-6]", tag):
            self.in_head = int(tag[1])
            self.head_text = []
            return
        if tag in ("b", "strong"):
            self.bold += 1
        if tag == "table":
            self.stack.append({"classes": (a.get("class") or "").split(), "rows": [], "row": None,
                               "cell": None, "spans": {}})
            return
        if not self.stack:
            return
        t = self.stack[-1]
        if tag == "tr":
            t["row"] = []
        elif tag in ("td", "th") and t["row"] is not None:
            t["cell"] = {"cell": Cell(header=tag == "th"), "text": [], "bold0": self.bold,
                         "rowspan": _int(a.get("rowspan")), "colspan": _int(a.get("colspan"))}
        elif tag == "a" and t["cell"] is not None and a.get("href"):
            t["cell"]["cell"].links.append(a["href"])
        elif tag == "br" and t["cell"] is not None:
            t["cell"]["text"].append(" ")

    def handle_endtag(self, tag):
        if tag in ("style", "script") or (tag == "sup" and self.skip):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.in_head and tag == f"h{self.in_head}":
            level = self.in_head
            self.heads = {k: v for k, v in self.heads.items() if k < level}
            self.heads[level] = clean("".join(self.head_text))
            self.in_head = None
            return
        if tag in ("b", "strong"):
            self.bold = max(0, self.bold - 1)
        if not self.stack:
            return
        t = self.stack[-1]
        if tag in ("td", "th") and t["cell"] is not None:
            self._close_cell(t)
        elif tag == "tr" and t["row"] is not None:
            if t["cell"] is not None:
                self._close_cell(t)
            self._close_row(t)
        elif tag == "table":
            if t["row"]:
                self._close_row(t)
            self.stack.pop()
            heads = [self.heads[k] for k in sorted(self.heads)]
            self.tables.append(Table(heads[-1] if heads else "", heads, t["classes"], t["rows"]))

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_head:
            self.head_text.append(data)
            return
        if self.stack and self.stack[-1]["cell"] is not None:
            c = self.stack[-1]["cell"]
            c["text"].append(data)
            if self.bold > c["bold0"] and data.strip():
                c["cell"].bold = True

    def _close_cell(self, t):
        c = t["cell"]
        c["cell"].text = clean("".join(c["text"]))
        t["row"].append((c["cell"], c["rowspan"] or 1, c["colspan"] or 1))
        t["cell"] = None

    def _close_row(self, t):
        cells, spans = t["row"], t["spans"]
        out: list[Cell] = []
        col = 0
        queue = list(cells)
        while queue or any(k >= col for k in spans):
            if col in spans:
                cell, left = spans[col]
                out.append(cell)
                if left <= 1:
                    del spans[col]
                else:
                    spans[col] = (cell, left - 1)
                col += 1
                continue
            if not queue:
                break
            cell, rs, cs = queue.pop(0)
            for _ in range(cs):
                out.append(cell)
                if rs > 1:
                    spans[col] = (cell, rs - 1)
                col += 1
        t["rows"].append(out)
        t["row"] = None


def _int(v: str | None) -> int | None:
    try:
        return max(1, int(re.sub(r"\D", "", v or "")))
    except ValueError:
        return None


def read_tables(page: str, cls: str | None = "wikitable") -> list[Table]:
    """Every table on the page (only those with class `cls`, unless None), in page order."""
    p = _Parser()
    p.feed(page)
    p.close()
    tables = p.tables
    if cls:
        tables = [t for t in tables if cls in t.classes]
    return tables


def wiki_title(href: str) -> str | None:
    """The article a Wikipedia link points at ("/wiki/Bouquet_of_Roses" -> "Bouquet of Roses")."""
    m = re.search(r"/wiki/([^#?]+)", href or "")
    if not m or ":" in m.group(1):
        return None
    from urllib.parse import unquote
    return unquote(m.group(1)).replace("_", " ")

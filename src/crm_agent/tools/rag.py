"""轻量 RAG 检索层：中文友好的 BM25，零外部依赖。

为什么不用向量库：
- 作品集要保证"clone 下来就能跑"，Chroma/FAISS 在 Windows + Python 3.13 上装不稳；
- 这套策略库只有几千字，BM25 的召回已经够用，而且**可解释**——评估时能直接看出
  某一句话为什么被召回，而不是调出一个 768 维向量说不清楚；
- 接口刻意设计成 retrieve(query, top_k)，后续换成 bge-m3 + Chroma 只需要替换实现。

中文分词没有引入 jieba，用的是"单字 + 相邻二元组"：
既能匹配"续费""流失预警""预算利用率"这类词，也不会因为分词错误丢召回。
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field


def tokenize(text: str) -> list[str]:
    text = text.lower()
    # 英文/数字按单词切
    latin = re.findall(r"[a-z0-9]+", text)
    # 中文只保留汉字，再做单字 + bigram
    han = re.findall(r"[\u4e00-\u9fff]", text)
    tokens = latin + han
    tokens += ["".join(pair) for pair in zip(han, han[1:])]
    return tokens


@dataclass
class Chunk:
    doc_id: str
    source: str          # 相对路径，用作引用来源
    title: str           # 文档标题 / 小节标题
    text: str
    tokens: list[str] = field(default_factory=list)


@dataclass
class Hit:
    chunk: Chunk
    score: float

    def as_dict(self) -> dict:
        return {
            "source": self.chunk.source,
            "title": self.chunk.title,
            "text": self.chunk.text,
            "score": round(self.score, 3),
        }


class KnowledgeBase:
    def __init__(self, knowledge_dir: str):
        self.knowledge_dir = knowledge_dir
        self.chunks: list[Chunk] = []
        self._df: Counter[str] = Counter()
        self._avg_len = 0.0
        self._load()

    # ---------- 建库 ----------
    def _load(self) -> None:
        for root, _dirs, files in os.walk(self.knowledge_dir):
            for name in sorted(files):
                if not name.endswith(".md"):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, self.knowledge_dir).replace("\\", "/")
                with open(path, "r", encoding="utf-8") as f:
                    self.chunks.extend(self._split(rel, f.read()))

        for chunk in self.chunks:
            chunk.tokens = tokenize(chunk.title + " " + chunk.text)
            self._df.update(set(chunk.tokens))
        self._avg_len = (
            sum(len(c.tokens) for c in self.chunks) / len(self.chunks) if self.chunks else 0.0
        )

    @staticmethod
    def _split(rel: str, text: str) -> list[Chunk]:
        """按二级标题切块，块内保留标题作为上下文前缀。"""
        parts = re.split(r"\n(?=## )", text)
        out: list[Chunk] = []
        doc_title = parts[0].splitlines()[0].lstrip("# ").strip() if parts else rel
        for i, part in enumerate(parts):
            body = part.strip()
            if not body:
                continue
            first_line = body.splitlines()[0].lstrip("# ").strip()
            title = first_line if first_line else doc_title
            if i == 0:
                title = doc_title
            out.append(
                Chunk(
                    doc_id=f"{rel}#{i}",
                    source=rel,
                    title=title,
                    text=body,
                )
            )
        return out

    # ---------- 检索 ----------
    def retrieve(self, query: str, top_k: int = 4, per_doc_limit: int = 2) -> list[Hit]:
        if not self.chunks:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        n_docs = len(self.chunks)
        k1, b = 1.5, 0.75
        idf = {
            t: math.log(1 + (n_docs - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5))
            for t in set(q_tokens)
        }

        scored: list[Hit] = []
        for chunk in self.chunks:
            tf = Counter(chunk.tokens)
            score = 0.0
            length = len(chunk.tokens) or 1
            for token in q_tokens:
                freq = tf.get(token, 0)
                if not freq:
                    continue
                score += idf[token] * (freq * (k1 + 1)) / (
                    freq + k1 * (1 - b + b * length / (self._avg_len or 1))
                )
            if score > 0:
                scored.append(Hit(chunk, score))

        scored.sort(key=lambda h: h.score, reverse=True)
        if not scored:
            return []

        # 分数天花板以下的噪声直接砍掉，避免"什么都引用一句"稀释可信度
        floor = scored[0].score * 0.2
        scored = [h for h in scored if h.score >= floor]

        # 同一文档最多取 per_doc_limit 块，避免一个文件霸屏
        picked: list[Hit] = []
        counts: Counter[str] = Counter()
        for hit in scored:
            if counts[hit.chunk.source] >= per_doc_limit:
                continue
            picked.append(hit)
            counts[hit.chunk.source] += 1
            if len(picked) >= top_k:
                break
        return picked

    def stats(self) -> dict:
        return {
            "chunks": len(self.chunks),
            "docs": len({c.source for c in self.chunks}),
            "vocab": len(self._df),
        }


def format_context(hits: list[Hit], max_chars: int = 1200) -> str:
    """把检索结果拼成给模型的上下文，带编号与来源，便于模型引用。"""
    lines = []
    used = 0
    for i, hit in enumerate(hits, 1):
        block = f"[{i}] 来源《{hit.chunk.source}》- {hit.chunk.title}\n{hit.chunk.text.strip()}"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return "\n\n".join(lines)

"""Read-only access to the knowledge map: which technology slugs exist.

The map is served as one columnar JSON document (``/api/graph`` on the server today, a static
``graph.json`` on GitHub Pages later). Only slugs, names and kinds are needed here.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import load_config

DEFAULT_SOURCE = load_config().kmap


@dataclass(frozen=True)
class KmapNode:
    slug: str
    kind: str
    name: str
    name_zh: str | None
    summary: str | None = None
    topics: tuple[str, ...] = ()  # slugs of the topics this node belongs to

    def as_dict(self) -> dict:
        return {"slug": self.slug, "kind": self.kind, "name": self.name, "name_zh": self.name_zh, "summary": self.summary}


class KmapIndex:
    def __init__(self, nodes: dict[str, KmapNode], source: str):
        self.nodes = nodes
        self.source = source

    def __contains__(self, slug: str) -> bool:
        return slug in self.nodes

    def get(self, slug: str) -> KmapNode | None:
        return self.nodes.get(slug)

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Find technology nodes for a free-text query (English or Chinese).

        Local substring match over slug / name / name_zh / summary, merged with the server's
        ``/api/search`` (aliases and trigram similarity) when the source is the live API.
        """
        q = query.strip().lower()
        scored: dict[str, float] = {}
        for n in self.nodes.values():
            names = [n.slug, n.name.lower(), (n.name_zh or "").lower()]
            if q in names or q.replace(" ", "-") == n.slug:
                s = 1.0
            elif any(q and q in x for x in names):
                s = 0.8
            elif any(x and x in q for x in names[1:] if len(x) >= 3):
                s = 0.6  # query mentions the node, e.g. "用 LoRA 微调"
            elif n.summary and q in n.summary.lower():
                s = 0.4
            else:
                continue
            scored[n.slug] = s
        for hit in self._api_search(query):
            slug = hit.get("slug")
            if slug in self.nodes:
                scored[slug] = max(scored.get(slug, 0), float(hit.get("score", 0.5)))
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return [{**self.nodes[s].as_dict(), "score": round(v, 3)} for s, v in ranked]

    def _api_search(self, query: str) -> list[dict]:
        if not self.source.endswith("/api/graph"):
            return []
        url = self.source[: -len("/graph")] + "/search?" + urllib.parse.urlencode({"q": query})
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - fixed read-only source
                return json.load(resp)
        except Exception:
            return []

    @classmethod
    def from_graph(cls, graph: dict, source: str) -> "KmapIndex":
        cols = graph["nodes"]
        if isinstance(cols, dict):  # columnar: {"slug": [...], "kind": [...], ...}
            n = len(cols["slug"])
            rows = [{k: cols[k][i] for k in cols} for i in range(n)]
        else:
            rows = cols
        # node "topics" are indices into the node arrays; topics form a DAG via "parents" (topic ids).
        slug_at = [r["slug"] for r in rows]
        topic_by_id = {t["id"]: t for t in graph.get("topics", [])}

        def ancestors(tid: str, seen: set[str]) -> None:
            t = topic_by_id.get(tid)
            if t and t["slug"] not in seen:
                seen.add(t["slug"])
                for parent in t.get("parents", []):
                    ancestors(parent, seen)

        id_at = [r.get("id") for r in rows]

        def tops(r) -> tuple[str, ...]:
            seen: set[str] = set()
            for i in r.get("topics") or []:
                if isinstance(i, int) and i < len(slug_at):
                    seen.add(slug_at[i])
                    ancestors(id_at[i], seen)
            return tuple(sorted(seen))
        nodes = {
            r["slug"]: KmapNode(r["slug"], r.get("kind", ""), r.get("name", ""), r.get("name_zh"), r.get("summary"), tops(r))
            for r in rows
            if r.get("layer", "knowledge") != "update"  # papers/releases/events are not technologies
        }
        return cls(nodes, source)

    @classmethod
    def load(cls, source: str = DEFAULT_SOURCE, timeout: float = 15) -> "KmapIndex":
        if source.startswith(("http://", "https://")):
            with urllib.request.urlopen(source, timeout=timeout) as resp:  # noqa: S310 - fixed read-only source
                graph = json.load(resp)
        else:
            graph = json.loads(Path(source).read_text(encoding="utf-8"))
        return cls.from_graph(graph, source)

"""Bounded, source-aware web search for 科举 answer evidence."""

from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests


DEFAULT_TIMEOUT = 2.5
DEFAULT_LIMIT_PER_PROVIDER = 3
# Bing serves a JavaScript shell without result blocks to some full browser user
# agents. Its lightweight response is both faster and directly parseable.
USER_AGENT = "Mozilla/5.0"


def clean_search_question(question: str) -> str:
    """Remove live score/round noise without rewriting the actual question."""
    text = unicodedata.normalize("NFKC", question or "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]{0,3}", "", text)
    text = re.sub(r"^\d{0,2}第\s*\d+(?:\s*/\s*\d+)?\s*题[-—:]?", "", text)
    text = re.sub(r"^第\s*\d+(?:\s*/\s*\d+)?\s*题[-—:]?", "", text)
    text = re.sub(r"^正确率[:：]?\s*\d+\s*/\s*\d+", "", text)
    text = re.sub(r"^当前得分[:：]?\s*\d+", "", text)
    text = re.sub(r"^剩余时间\s*\d+", "", text)
    return text.strip("-—:：,，.。?？!！ ")


class _Element:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.parent = parent
        self.children = []
        self.content = []


class _DOMParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element()
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = _Element(tag.lower(), attrs, self.current)
        self.current.children.append(node)
        self.current.content.append(node)
        if tag.lower() not in self.VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        node = _Element(tag.lower(), attrs, self.current)
        self.current.children.append(node)
        self.current.content.append(node)

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root:
            if node.tag == tag.lower():
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.content.append(data)


def _parse_document(payload: str) -> _Element:
    parser = _DOMParser()
    parser.feed(payload)
    parser.close()
    return parser.root


def _walk(node):
    for child in node.children:
        yield child
        yield from _walk(child)


def _has_class(node, class_name: str) -> bool:
    return class_name in node.attrs.get("class", "").split()


def _node_text(node) -> str:
    parts = [item if isinstance(item, str) else _node_text(item) for item in node.content]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _first_descendant(node, predicate):
    return next((item for item in _walk(node) if predicate(item)), None)


def _children(node, tag):
    return [child for child in node.children if child.tag == tag]


def _direct_bing_answer(root):
    main = next(
        (node for node in _walk(root) if node.attrs.get("id") == "gs_main"),
        None,
    )
    if main is None:
        return []
    node = main
    try:
        for index in (1, 1, 0, 0, 1, 0):
            node = _children(node, "div")[index]
    except IndexError:
        node = None
    exact = _children(node, "span") if node is not None else []
    semantic = [
        item
        for item in _walk(main)
        if _has_class(item, "b_focusTextLarge") or _has_class(item, "b_entityTitle")
    ]
    return exact + semantic


def parse_bing_html(payload: str, limit: int = DEFAULT_LIMIT_PER_PROVIDER) -> list[dict]:
    """Extract Bing's direct answer and ordinary result snippets."""
    if not payload.strip():
        return []
    document = _parse_document(payload)
    evidence = []

    # Bing currently exposes its best-match answer at this path in the rendered
    # result page. Semantic fallbacks cover minor layout revisions.
    direct_nodes = _direct_bing_answer(document)
    seen_direct = set()
    for node in direct_nodes:
        answer = _node_text(node)
        normalized = re.sub(r"\s+", "", answer)
        if not answer or normalized in seen_direct or len(answer) > 240:
            continue
        seen_direct.add(normalized)
        evidence.append({
            "provider": "bing",
            "kind": "direct_answer",
            "title": "Bing 最佳匹配答案",
            "snippet": answer,
            "url": "https://www.bing.com/search",
            "rank": 0,
        })

    result_nodes = [
        node
        for node in _walk(document)
        if node.tag == "li" and _has_class(node, "b_algo")
    ]
    for rank, node in enumerate(result_nodes, start=1):
        heading = _first_descendant(node, lambda item: item.tag == "h2")
        link = (
            _first_descendant(heading, lambda item: item.tag == "a")
            if heading
            else None
        )
        caption = _first_descendant(node, lambda item: _has_class(item, "b_caption"))
        paragraph = (
            _first_descendant(caption, lambda item: item.tag == "p")
            if caption
            else None
        )
        title = _node_text(link) if link else ""
        snippet = _node_text(paragraph) if paragraph else ""
        url = link.attrs.get("href", "").strip() if link else ""
        if not title and not snippet:
            continue
        evidence.append({
            "provider": "bing",
            "kind": "result",
            "title": title[:240],
            "snippet": snippet[:600],
            "url": url[:1000],
            "rank": rank,
        })
        if rank >= limit:
            break
    return evidence


def parse_so360_html(payload: str, limit: int = DEFAULT_LIMIT_PER_PROVIDER) -> list[dict]:
    """Extract ordinary 360 Search results as an independent evidence channel."""
    if not payload.strip():
        return []
    document = _parse_document(payload)
    evidence = []
    result_nodes = [
        node
        for node in _walk(document)
        if node.tag == "li" and _has_class(node, "res-list")
    ]
    for node in result_nodes:
        heading = _first_descendant(node, lambda item: item.tag == "h3")
        link = (
            _first_descendant(heading, lambda item: item.tag == "a")
            if heading
            else None
        )
        summary = _first_descendant(
            node,
            lambda item: _has_class(item, "res-desc")
            or _has_class(item, "res-list-summary"),
        )
        title = _node_text(link) if link else ""
        snippet = _node_text(summary) if summary else ""
        url = (
            link.attrs.get("data-mdurl", "") or link.attrs.get("href", "")
            if link
            else ""
        )
        if not title or not snippet:
            continue
        evidence.append({
            "provider": "so360",
            "kind": "result",
            "title": title[:240],
            "snippet": snippet[:600],
            "url": urljoin("https://www.so.com", url)[:1000],
            "rank": len(evidence) + 1,
        })
        if len(evidence) >= limit:
            break
    return evidence


def _http_get(url: str, params: dict, timeout: float) -> str:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def search_bing(query: str, timeout: float, limit: int) -> list[dict]:
    payload = _http_get(
        "https://www.bing.com/search",
        {"form": "QBLH", "q": query},
        timeout,
    )
    return parse_bing_html(payload, limit=limit)


def search_so360(query: str, timeout: float, limit: int) -> list[dict]:
    payload = _http_get(
        "https://www.so.com/s",
        {"q": f"梦幻西游手游 {query}"},
        timeout,
    )
    return parse_so360_html(payload, limit=limit)


DEFAULT_PROVIDERS = {
    "bing": search_bing,
    "so360": search_so360,
}


def _deduplicate(evidence: list[dict]) -> list[dict]:
    output = []
    seen = set()
    for item in evidence:
        key = re.sub(
            r"\W+", "", f"{item.get('title', '')}{item.get('snippet', '')}"
        ).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def search_web_evidence(
    question: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    limit_per_provider: int = DEFAULT_LIMIT_PER_PROVIDER,
    providers: dict | None = None,
    provider_names: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Query independent providers concurrently and return bounded diagnostics."""
    query = clean_search_question(question)
    available = providers if providers is not None else DEFAULT_PROVIDERS
    selected = (
        {name: available[name] for name in provider_names if name in available}
        if provider_names is not None
        else available
    )
    started = time.perf_counter()
    diagnostics = {}
    evidence = []
    if not query or not selected:
        return {
            "query": query,
            "evidence": [],
            "providers": {},
            "elapsed_ms": 0,
        }

    def run_provider(name, provider):
        provider_started = time.perf_counter()
        try:
            items = provider(query, timeout, limit_per_provider)
            elapsed_ms = round((time.perf_counter() - provider_started) * 1000)
            return name, items, None, elapsed_ms
        except (requests.RequestException, ValueError, TypeError) as exc:
            return (
                name,
                [],
                f"{type(exc).__name__}: {str(exc)[:160]}",
                round((time.perf_counter() - provider_started) * 1000),
            )

    with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="keju-search") as pool:
        futures = [
            pool.submit(run_provider, name, provider)
            for name, provider in selected.items()
        ]
        for future in as_completed(futures):
            name, items, error, elapsed_ms = future.result()
            diagnostics[name] = {
                "elapsed_ms": elapsed_ms,
                "result_count": len(items),
                "error": error,
            }
            evidence.extend(items)

    evidence.sort(
        key=lambda item: (
            0 if item.get("kind") == "direct_answer" else 1,
            item.get("rank", 999),
            item.get("provider", ""),
        )
    )
    evidence = _deduplicate(evidence)
    for index, item in enumerate(evidence, start=1):
        item["id"] = f"web-{index}"
    return {
        "query": query,
        "evidence": evidence,
        "providers": diagnostics,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


__all__ = [
    "clean_search_question",
    "parse_bing_html",
    "parse_so360_html",
    "search_web_evidence",
]

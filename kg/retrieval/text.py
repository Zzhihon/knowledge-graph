"""Chinese-aware tokenising for BM25: jieba search-mode cut, lower-cased, function words dropped.

Without this, a Chinese query is one long token and BM25 matches almost nothing. Entry tags are
added to jieba's dictionary so terms like `argocd` or `前缀缓存` stay whole.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# Function words and question fillers: they appear in most questions and carry no topic.
_STOP_WORDS = """
的 了 是 在 和 与 及 或 也 都 就 还 又 而 但 被 把 让 给 对
从 向 到 为 以 于 之 其 这 那 这个 那个 这些 那些 这样 那样 这种
那种 什么 怎么 怎么样 怎样 如何 为什么 为何 哪些 哪个 哪里 多少 是否
能不能 有没有 可以 可能 应该 需要 要 会 能 吗 呢 吧 啊 呀 嘛 哦
么 我 你 他 她 它 我们 你们 他们 咱们 自己 一个 一些 一下 有 没有
没 不 不是 很 更 最 再 又 就是 还是 然后 所以 因为 如果 但是 而且
并且 以及 现在 之前 之后 时候 里 上 下 中 个 些 等 啥 呗 原因
问题 意思 东西 情况 方面 一般 通常 比较 主要 具体 下面 上面 刚才
the a an of to in on for and or is are
be with how what why which does do can
"""
_STOP = frozenset(_STOP_WORDS.split())
_PUNCT = re.compile(r"^[\W_]+$")
_lock = threading.Lock()
_ready = False


def _jieba():  # type: ignore[no-untyped-def]
    import jieba

    global _ready
    if not _ready:
        with _lock:
            if not _ready:
                jieba.setLogLevel(logging.WARNING)
                jieba.initialize()
                _ready = True
    return jieba


def add_terms(terms: Iterable[str]) -> None:
    """Teach jieba domain terms (entry tags) so they are not split apart."""
    jieba = _jieba()
    for term in terms:
        term = str(term).strip().lower()
        if 2 <= len(term) <= 30 and not _PUNCT.match(term):
            jieba.add_word(term, freq=2000)


def tokenize(text: str) -> list[str]:
    jieba = _jieba()
    out = []
    for tok in jieba.lcut_for_search(text.lower()):
        tok = tok.strip()
        if not tok or tok in _STOP or _PUNCT.match(tok):
            continue
        out.append(tok)
    return out

"""把法律全文切成「条文号 → 条文全文」。

过程层评分（召回命中率、MRR）全靠这个映射：只有能定位到具体条文，
才能判断"检索有没有把被问到的那一条召回回来"。
"""

from __future__ import annotations

import re
from pathlib import Path

ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零]+条")

# 语料文件名（不含扩展名）到法律全称
LAW_NAMES: dict[str, str] = {
    "pipl": "个人信息保护法",
    "dsl": "数据安全法",
    "csl": "网络安全法",
}


class SourceRefError(ValueError):
    """条文引用格式不合法。"""


def parse_source(source: str) -> tuple[str, str]:
    """把 "pipl:第五条" 拆成 ("pipl", "第五条")。

    条文号在**不同法律之间不唯一**（三部法律都有"第五条"），
    所以引用必须带上法律限定，否则既无法校验也无法做召回命中判断。
    """
    law, sep, article = source.partition(":")
    if not sep or not law or not article:
        raise SourceRefError(
            f"条文引用必须写成「法律:条文号」形式（如 pipl:第五条），收到 {source!r}"
        )
    if law not in LAW_NAMES:
        raise SourceRefError(f"未知的法律代号 {law!r}，可选：{', '.join(LAW_NAMES)}")
    if not ARTICLE_RE.fullmatch(article):
        raise SourceRefError(f"条文号格式不对：{article!r}")
    return law, article


def find_article(
    corpus_articles: dict[str, dict[str, str]], law: str, article: str
) -> str:
    """按法律 + 条文号取原文。"""
    per_file = corpus_articles.get(f"{law}.txt")
    if per_file is None:
        raise SourceRefError(f"语料中找不到 {law}.txt")
    return per_file.get(article, "")


def split_articles(law_text: str) -> dict[str, str]:
    """返回 {条文号: 该条文本}，条文号形如「第十三条」。"""
    matches = list(ARTICLE_RE.finditer(law_text))
    if not matches:
        return {}

    articles: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(law_text)
        # 同名条文取首次出现，避免目录/引用干扰
        articles.setdefault(match.group(), law_text[start:end].strip())
    return articles


def load_corpus_articles(corpus_dir: Path) -> dict[str, dict[str, str]]:
    """返回 {文件名: {条文号: 条文文本}}。"""
    result: dict[str, dict[str, str]] = {}
    for path in sorted(corpus_dir.glob("*.txt")):
        result[path.name] = split_articles(path.read_text(encoding="utf-8"))
    return result

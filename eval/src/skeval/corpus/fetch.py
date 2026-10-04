"""从中国人大网抓取三法全文。

三个来源都是官方公开页面：

  个人信息保护法  http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html
  数据安全法      http://www.npc.gov.cn/c2/c30834/202106/t20210610_311888.html
  网络安全法      http://www.npc.gov.cn/zgrdw/npc/xinwen/2016-11/07/content_2001605.htm

两个必须处理的坑，都来自实测：

  1. 人大网在中文标点两侧插入了空格（"网络安全 ， 维护"）。不清掉会污染分块，
     也会让 BM25 之类的词法检索把标点当成独立词。
  2. 页面首尾有导航与页脚（"当前位置：首页 ..."、"责任编辑"），必须切除，
     否则会混进语料、干扰评测。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

_CJK = r"\u4e00-\u9fff"
_CJK_PUNCT = "，。、；：？！（）《》“”‘’·—…"

# 条文编号，用于定位正文范围与校验完整性
_ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零]+条")
# 中文标点或汉字之间的空白，全都要删掉
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}{_CJK_PUNCT}])\s+(?=[{_CJK}{_CJK_PUNCT}])")
# 标题「（…通过）」之后、正文之前，会残留各种页面 chrome：
# 来源、浏览字号、日期时间，以及剥标签后剩下的 "enpproperty-->" 碎片。
# 与其逐个关键字清理，不如统一从标题右括号一路删到正文起点。
_PRE_BODY_RE = re.compile(r"^(.*?）).*?(?=目录|第[一二三四五六七八九十]+章|第一条)", re.S)
# 标题与「第一条」之间最多允许多少字符。超过说明 rfind 命中的是别处
# （比如 <title> 或导航），此时退回「第一条」本身。
_TITLE_GAP_LIMIT = 500


@dataclass(frozen=True, slots=True)
class LawSource:
    slug: str
    title: str
    url: str


LAWS: tuple[LawSource, ...] = (
    LawSource(
        slug="pipl",
        title="中华人民共和国个人信息保护法",
        url="http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html",
    ),
    LawSource(
        slug="dsl",
        title="中华人民共和国数据安全法",
        url="http://www.npc.gov.cn/c2/c30834/202106/t20210610_311888.html",
    ),
    LawSource(
        slug="csl",
        title="中华人民共和国网络安全法",
        url="http://www.npc.gov.cn/zgrdw/npc/xinwen/2016-11/07/content_2001605.htm",
    ),
)


def fetch_html(url: str, timeout_s: float = 30.0) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Referer": "http://www.npc.gov.cn/",
    }
    response = httpx.get(url, headers=headers, timeout=timeout_s, follow_redirects=True)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def html_to_text(html: str) -> str:
    """剥掉标签，得到连续文本。"""
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def clean_cjk_spacing(text: str) -> str:
    """删掉中文标点/汉字之间被插入的空格。"""
    previous = None
    while previous != text:
        previous = text
        text = _CJK_SPACE_RE.sub("", text)
    return text


def extract_law_body(text: str, title: str) -> str:
    """截取从标题到最后一个条文的正文。"""
    first = text.find("第一条")
    if first < 0:
        raise ValueError("正文中找不到「第一条」，页面结构可能已变化")

    # 「第一条」之前最后一次出现标题的位置，通常就是页面正文的标题行
    start = text.rfind(title, 0, first)
    if start < 0 or first - start > _TITLE_GAP_LIMIT:
        start = first

    articles = list(_ARTICLE_RE.finditer(text))
    end = len(text)
    if articles:
        tail = text.find("。", articles[-1].end())
        end = tail + 1 if tail > 0 else articles[-1].end()

    # 正文范围由「最后一个条文」决定，页脚天然被排除在外。
    # 不要再用页脚关键字去裁剪：像「来源：」这类词在正文开头也会出现，
    # 会把正文从标题处就切断（实测踩过）。
    body = clean_cjk_spacing(text[start:end]).strip()
    return _PRE_BODY_RE.sub(r"\1", body, count=1).strip()


def article_count(text: str) -> int:
    """统计条文编号的出现次数，用于校验抓取完整性。"""
    return len(_ARTICLE_RE.findall(text))


def last_article(text: str) -> str:
    matches = _ARTICLE_RE.findall(text)
    return matches[-1] if matches else ""


def fetch_law(source: LawSource, out_dir: Path, timeout_s: float = 30.0) -> Path:
    html = fetch_html(source.url, timeout_s=timeout_s)
    body = extract_law_body(html_to_text(html), source.title)
    if article_count(body) < 20:
        raise ValueError(f"{source.title} 提取到的条文过少（{article_count(body)}），拒绝写入")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source.slug}.txt"
    out_path.write_text(body, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skeval.corpus.fetch", description="抓取三法全文")
    parser.add_argument("--out-dir", default="corpus/raw", help="输出目录（默认 corpus/raw）")
    parser.add_argument("--only", help="只抓取指定 slug：pipl / dsl / csl")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    targets = [s for s in LAWS if not args.only or s.slug == args.only]
    if not targets:
        print(f"未知的 slug：{args.only}", file=sys.stderr)
        return 2

    failures = 0
    for source in targets:
        try:
            path = fetch_law(source, out_dir)
            body = path.read_text(encoding="utf-8")
            print(
                f"{source.title}\n"
                f"  来源: {source.url}\n"
                f"  文件: {path}  {len(body)} 字  {article_count(body)} 处条文编号  "
                f"末条 {last_article(body)}"
            )
        except Exception as exc:  # noqa: BLE001 - 抓取失败要显式报告，不能静默
            failures += 1
            print(f"{source.title} 抓取失败: {type(exc).__name__}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

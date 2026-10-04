"""规则评分器。

刻意先做规则、不做 LLM 裁判（project.md 第 4 节的顺序约束）：
没有规则基线，就无法判断 LLM 裁判本身准不准。

分两层：
  结果层 —— 答案对不对
  过程层 —— 检索有没有把该召回的内容召回回来（本项目相对通用框架的差异所在）
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass

from ..corpus.articles import parse_source
from ..dataset import Case
from ..retrieval import RetrievedChunk

# 表示"答不出来"的常见措辞。
# 注意：只有出现在**回答开头**才算拒答（见 REFUSAL_PROBE_CHARS）。
# 因为"超出""不存在"这类词在正常法条回答里到处都是（"不得超出必要范围"），
# 全篇搜会大量误报——实测踩过。
REFUSAL_MARKERS = (
    "无法回答",
    "无法直接回答",
    "不能回答",
    "无法确定",
    "无法提供",
    "无从",
    "没有包含",
    "未包含",
    "不包含",
    "没有相关",
    "未找到",
    "没有找到",
    "不存在",
    "超出",
    "建议您查阅",
)

# 只在回答前这么多字符里找拒答措辞。拒答通常开门见山。
REFUSAL_PROBE_CHARS = 150

# 语义相似度的通过阈值。**这是一个起点，应当用数据校准**：
# 拿一批人评为"等价"和"不等价"的答案各跑一遍，看分数分布在哪里分开。
# 在没做这件事之前，它只用来做趋势对比，不应当当作绝对判据。
SEMANTIC_PASS_THRESHOLD = 0.7

# 评分器清单。顺序即报告里的展示顺序。
# 冻结进 run 的配置快照（project.md R3）：评分器变了，两次 run 就不再可比，
# 这一点必须能从记录里看出来。
SCORER_NAMES = (
    "retrieval_hit",
    "citation_support",
    "semantic_similarity",
    "points_hit",
    "refusal",
)

# Markdown 标记与空白：比对前要删掉。
# 模型很爱把关键词加粗（"建立健全**全流程数据安全管理制度**"），
# 直接用原始文本做子串匹配会被 ** 切断，产生大量假失败——实测踩过。
_MARKUP_RE = re.compile(r"[\s*`_#>~]+")

# 答案里引用的条文号。与语料侧的正则保持一致（中文数字 + 阿拉伯数字）。
_ARTICLE_REF_RE = re.compile(r"第[一二三四五六七八九十百零\d]+条")


def _normalize(text: str) -> str:
    return _MARKUP_RE.sub("", text)


@dataclass(frozen=True, slots=True)
class Score:
    name: str
    layer: str  # outcome / process
    value: float  # 0..1
    passed: bool
    reason: str
    # 该指标对这一条是否适用。拒答题在 retrieval_hit / citation_support / points_hit 上
    # 天然无从判定——把它们当成"通过"计进分母，等于**白送分数**，会让通过率虚高。
    # 显式标出来，汇总时才能把它们排除在分子分母之外。
    applicable: bool = True


def _not_applicable(name: str, layer: str, reason: str) -> Score:
    return Score(name, layer, 1.0, True, reason, applicable=False)


def build_source_law_map(documents: list[dict]) -> dict[str, str]:
    """建立「MinIO 地址 -> 法律代号」的映射。

    必须走这一步：MinIO 上传时会把文件重命名成内容的 MD5
    （如 knowledge/9222edef….txt），所以召回片段的 meta_source 里
    **没有原始文件名**，无法直接判断片段来自哪部法律。
    只有 knowledge_doc 表同时保留了 url 与 doc_name，能把它接回来。
    """
    mapping: dict[str, str] = {}
    for doc in documents:
        url = str(doc.get("url") or "")
        name = str(doc.get("docName") or doc.get("doc_name") or "")
        if url and name:
            mapping[url] = Path(name).stem
    return mapping


def law_of_source(source: str, source_law_map: dict[str, str]) -> str | None:
    """判断某个召回片段来自哪部法律；认不出来返回 None。"""
    if source in source_law_map:
        return source_law_map[source]
    stem = Path(source).stem
    for url, law in source_law_map.items():
        if Path(url).stem == stem:
            return law
    return None


def score_points_hit(case: Case, answer: str) -> Score:
    """结果层：答案覆盖了多少个期望要点。"""
    if not case.expected_points:
        return _not_applicable("points_hit", "outcome", "该条目不适用")
    normalized = _normalize(answer)
    hits = [p for p in case.expected_points if _normalize(p) in normalized]
    missing = [p for p in case.expected_points if _normalize(p) not in normalized]
    value = len(hits) / len(case.expected_points)
    reason = f"命中 {len(hits)}/{len(case.expected_points)}"
    if missing:
        reason += "；缺：" + "、".join(missing)
    return Score("points_hit", "outcome", value, value == 1.0, reason)


def score_refusal(case: Case, answer: str) -> Score:
    """结果层：该拒答时是否拒答了；不该拒答时是否也没有无端拒答。"""
    opening = _normalize(answer)[:REFUSAL_PROBE_CHARS]
    refused = any(marker in opening for marker in REFUSAL_MARKERS)
    if case.should_refuse:
        return Score(
            "refusal",
            "outcome",
            1.0 if refused else 0.0,
            refused,
            "正确拒答" if refused else "本应拒答却给出了答案（幻觉风险）",
        )
    return Score(
        "refusal",
        "outcome",
        0.0 if refused else 1.0,
        not refused,
        "无端拒答" if refused else "正常作答",
    )


def score_retrieval(
    case: Case, chunks: list[RetrievedChunk], source_law_map: dict[str, str]
) -> Score:
    """过程层：期望条文是否被召回、排在第几位。

    判定条件：某个召回片段要同时满足
      (a) 来自期望的那部法律，(b) 文本里出现该条文的条文号。
    """
    if case.should_refuse or not case.expected_sources:
        return _not_applicable("retrieval_hit", "process", "该条目不适用")

    wanted = [parse_source(s) for s in case.expected_sources]
    best_rank: int | None = None
    for chunk in chunks:
        law_of_chunk = law_of_source(chunk.source, source_law_map)
        if law_of_chunk is None:
            continue
        for law, article in wanted:
            if law_of_chunk == law and article in chunk.content:
                best_rank = chunk.rank if best_rank is None else min(best_rank, chunk.rank)

    if best_rank is None:
        return Score(
            "retrieval_hit",
            "process",
            0.0,
            False,
            f"期望条文 {', '.join(case.expected_sources)} 未出现在召回的 {len(chunks)} 个片段中",
        )
    return Score(
        "retrieval_hit",
        "process",
        1.0 / best_rank,
        True,
        f"期望条文命中，最优排名第 {best_rank} 位（MRR={1.0 / best_rank:.2f}）",
    )


def score_citation_support(
    case: Case, answer: str, chunks: list[RetrievedChunk]
) -> Score:
    """过程层：答案引用的条文号，能不能在**被召回的片段**里找到出处。

    为什么需要它：法规问答里最高频的幻觉形态是**编造条文编号**，
    而它用纯文本就能精确判定，不需要 LLM 裁判。
    "引用了召回内容里根本不存在的条文号"是幻觉的**直接证据**，
    比"检索没命中但要点命中了"这种间接推断更硬。

    局限（如实说明，不要当成全量判定）：只比对条文号，不比对是**哪部法律**的条文号。
    三部法律的条文号会重号（三部都有"第五条"），所以本指标是**下界**——
    它判为"无出处"的一定可疑，判为"有出处"的仍可能是张冠李戴。
    """
    if case.should_refuse:
        return _not_applicable("citation_support", "process", "该条目不适用")

    cited = list(dict.fromkeys(_ARTICLE_REF_RE.findall(answer)))
    if not cited:
        return _not_applicable("citation_support", "process", "答案未引用条文号，无从判定")

    context = "\n".join(chunk.content for chunk in chunks)
    missing = [no for no in cited if no not in context]
    supported = len(cited) - len(missing)
    reason = f"引用 {len(cited)} 个条文号，有出处 {supported} 个"
    if missing:
        reason += "；无出处：" + "、".join(missing)
    return Score("citation_support", "process", supported / len(cited), not missing, reason)


def score_semantic_similarity(case: Case, similarity: float | None) -> Score:
    """结果层：答案与参考答案的向量余弦相似度。

    向量由 runner 预先算好传进来——评分器保持纯同步、可单测，不在这里发网络请求。
    """
    if case.should_refuse:
        # 拒答条目的参考答案只是一句"该说没有"，措辞无标准，比余弦相似度没有意义。
        # 拒答正确性由 refusal 评分器负责，这里明确不适用，避免拉低结果层均值。
        return _not_applicable("semantic_similarity", "outcome", "该条目不适用")
    if not case.reference_answer:
        return _not_applicable("semantic_similarity", "outcome", "该条目没有参考答案，不适用")
    if similarity is None:
        return _not_applicable("semantic_similarity", "outcome", "未计算（嵌入不可用），不适用")
    value = max(0.0, min(1.0, similarity))
    return Score(
        "semantic_similarity",
        "outcome",
        value,
        similarity >= SEMANTIC_PASS_THRESHOLD,
        f"余弦相似度 {similarity:.3f}（阈值 {SEMANTIC_PASS_THRESHOLD}）",
    )


def score_all(
    case: Case,
    answer: str,
    chunks: list[RetrievedChunk],
    source_law_map: dict[str, str],
    *,
    semantic_similarity: float | None = None,
) -> list[Score]:
    return [
        score_retrieval(case, chunks, source_law_map),
        score_citation_support(case, answer, chunks),
        score_semantic_similarity(case, semantic_similarity),
        score_points_hit(case, answer),
        score_refusal(case, answer),
    ]

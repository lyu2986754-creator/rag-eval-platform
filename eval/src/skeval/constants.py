"""集中管理常量。业务代码里不允许出现魔法字符串。

注意：**失败归因的分类名不在这里**，而在 `report.py`——它是 `classify()` 的产出，
和判定逻辑放在一起才不会漂移。这里有且只有一份定义，是刻意的。
"""

from __future__ import annotations

from enum import Enum


class SseEvent(str, Enum):
    """sk-knowledge SSE 端点推送的事件名。

    对应 ChatCompletionsServiceImpl 里三次 sseEmitter.send 的 name。
    """

    CONTEXT = "chatContextIdVo"  # 会话与消息 id、窗口标题
    CONTENT = "content"  # 答案增量
    # 诊断事件：被评测对象的只读诊断输出，含真实召回片段与 token 用量。
    # 有了它，过程层归因才建立在实测而非代理之上。
    DIAGNOSTICS = "diagnostics"
    DONE = "done"  # 结束帧，携带完整助手消息


class RecordStatus(str, Enum):
    """单条评测记录的状态（project.md R4）。"""

    OK = "ok"
    RAG_ERROR = "rag_error"
    # 超时单独成一类：它和"服务返回了错误"的排查方向不同
    # （前者看网络与模型速度，后者看服务端日志）。
    TIMEOUT = "timeout"


class CaseCategory(str, Enum):
    """评测条目的题型（project.md 第 4 节要求的五类）。"""

    DIRECT = "direct_extraction"  # 直接抽取
    CROSS = "cross_article"  # 跨条文综合
    CONDITIONAL = "conditional"  # 条件与例外
    TERMINOLOGY = "terminology"  # 术语辨析
    REFUSE = "refuse"  # 应拒答


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


# 单次问答的超时（秒）。取值偏保守，因为"模型慢"与"调用失败"必须区分开——
# 把慢误判成失败会污染数据，而多等一会儿只是慢，不会错。
# 历史背景：早期用推理模型 Qwen3-8B 时，一个 18 token 的问题实测要 94 秒；
# 现已换成 deepseek-chat（实测 3.8 秒），但这个余量保留着，也覆盖多轮与 Agentic 的累积耗时。
# 服务端 SseEmitter 的超时是 5 分钟，180 秒不会先于它触发。
DEFAULT_TIMEOUT_S = 180.0

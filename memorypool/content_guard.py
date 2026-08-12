"""写入内容审查：检索结果会进每个 agent 的上下文，写入面就是注入面。

三类拦截（fail-closed，命中即拒绝写入）：
1. 提示注入指令——「忽略之前的指令」类命令式话术（模式刻意收窄：只拦命令式
   祈使句，不拦"角色扮演"类描述性词——那类在合法的会议记录/转述里高频出现，
   误杀比漏放代价大）
2. 隐形 unicode——零宽字符 / bidi 控制符，肉眼不可见但会被 LLM 读到，
   是隐藏注入的标准载体；正常记忆没有理由包含它们
3. 高置信凭证——sk-/ghp_/AKIA/xox/私钥块等强格式 token。共享池不加密不脱敏，
   「不写密码 token」的约定由这里强制兜底

设计参照 AgentClaw 的 scanMemoryContent（其 remember 工具的写前扫描）。
"""
from __future__ import annotations

import re

# 命令式提示注入（中英），刻意收窄避免误杀正常转述
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(ignore|disregard|forget)\s+(all\s+|any\s+)?"
            r"(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?)",
            re.IGNORECASE,
        ),
        "提示注入（ignore previous instructions）",
    ),
    (
        re.compile(r"忽略(之前|此前|以上|上面|全部|所有)的?(指令|规则|提示词?|设定|约束)"),
        "提示注入（忽略指令）",
    ),
    (
        re.compile(r"(无视|绕过|不要遵守)(安全|系统)?(规则|限制|约束|指令)"),
        "提示注入（绕过限制）",
    ),
]

# 零宽/bidi 控制符（合法记忆没有理由包含）
_INVISIBLE = re.compile(
    "[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)

# 高置信凭证格式（强前缀，误杀率低）
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "疑似 API key（sk- 前缀长串）"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GitHub personal access token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{36,}"), "GitHub fine-grained token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (
        re.compile(r"-----BEGIN\s+(?:RSA|EC|OPENSSH|PGP|DSA)?\s*PRIVATE KEY-----"),
        "私钥块",
    ),
]


class ContentRejected(ValueError):
    """写入内容未通过审查。message 说明命中原因，不回显完整命中片段。"""


def scan_content(text: str) -> list[str]:
    """返回命中的问题清单；空列表 = 通过。"""
    issues: list[str] = []
    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(text):
            issues.append(label)
    if _INVISIBLE.search(text):
        issues.append("隐形 unicode 字符（零宽/bidi 控制符）")
    for pattern, label in _SECRET_PATTERNS:
        if pattern.search(text):
            issues.append(label)
    return issues


def ensure_clean(text: str) -> None:
    """命中任一问题即抛 ContentRejected（写入链路的强制门）。"""
    issues = scan_content(text)
    if issues:
        raise ContentRejected(
            "写入被内容审查拒绝：" + "；".join(issues)
            + "。共享池不加密不脱敏，凭证类内容一律不收；注入类话术请改为转述描述。"
        )

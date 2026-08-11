"""时间维度工具（决策7/8/9）。

mem0 写入时在 payload 盖 `created_at = datetime.now(timezone.utc).isoformat()`。
本模块只做一件事：把这个绝对 UTC 时刻，在检索时现算成 AI 友好的相对时间（"3天前"），
注入 context。一列存储、两种呈现。

时间只做「给 AI 的参考信号」，不做系统自动裁决（不 last-write-wins 自动覆盖）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def parse_created_at(value: str) -> Optional[datetime]:
    """解析 mem0 的 created_at（ISO 8601，带 tz）。解析失败返回 None，不抛。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    # mem0 存的是带 tz 的 UTC；若碰到裸时间，按 UTC 处理
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def relative_time(created_at: str, now: Optional[datetime] = None) -> str:
    """把绝对时间转成中文相对时间，给 AI 判新旧/排顺序用。

    解析不了就原样返回，不让格式问题挡住检索。
    """
    dt = parse_created_at(created_at)
    if dt is None:
        return created_at or "未知时间"

    now = now or datetime.now(timezone.utc)
    delta = now - dt
    secs = delta.total_seconds()

    if secs < 0:
        return "刚刚"
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)}分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)}小时前"
    days = int(secs // 86400)
    if days < 30:
        return f"{days}天前"
    if days < 365:
        return f"{days // 30}个月前"
    return f"{days // 365}年前"


def annotate_relative_time(memory_item: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    """给单条检索结果补一个 `age` 字段（相对时间），原字段不动。

    mem0 的检索结果里 created_at 可能在顶层或 metadata 里，两处都查。
    """
    created = memory_item.get("created_at")
    if created is None and isinstance(memory_item.get("metadata"), dict):
        created = memory_item["metadata"].get("created_at")
    if created:
        memory_item = {**memory_item, "age": relative_time(created, now)}
    return memory_item


def annotate_results(results: Any, now: Optional[datetime] = None) -> Any:
    """给 mem0 search 的返回结果批量注入相对时间。

    mem0 的 search 返回 {"results": [...]} 或直接 [...]，两种都兼容。
    """
    if isinstance(results, dict) and "results" in results:
        annotated = [annotate_relative_time(m, now) for m in results["results"]]
        return {**results, "results": annotated}
    if isinstance(results, list):
        return [annotate_relative_time(m, now) for m in results]
    return results

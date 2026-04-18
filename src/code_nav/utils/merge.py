"""多引擎结果合并与去重。

合并策略：
- 按 (file, line) 去重，同一位置保留置信度最高的结果
- 置信度排序：HIGH > MEDIUM > LOW
- 同等置信度时保留排在前面的引擎结果（pyright 优先于 ast-grep）
"""
from __future__ import annotations

from code_nav.models import Reference, Definition, Confidence

_CONFIDENCE_ORDER = {
    Confidence.HIGH: 2,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 0,
}


def merge_references(refs: list[Reference]) -> list[Reference]:
    """合并多引擎的引用结果，按 (file, line) 去重。

    保留置信度更高的版本；同等置信度保留先出现的（引擎优先级决定）。
    """
    best: dict[tuple[str, int], Reference] = {}
    for r in refs:
        key = (r.file, r.line)
        existing = best.get(key)
        if existing is None:
            best[key] = r
        elif _CONFIDENCE_ORDER.get(r.confidence, 0) > _CONFIDENCE_ORDER.get(existing.confidence, 0):
            best[key] = r
    return list(best.values())


def merge_definitions(defs: list[Definition]) -> list[Definition]:
    """合并多引擎的定义结果，按 (file, line) 去重。"""
    best: dict[tuple[str, int], Definition] = {}
    for d in defs:
        key = (d.file, d.line)
        existing = best.get(key)
        if existing is None:
            best[key] = d
        elif _CONFIDENCE_ORDER.get(d.confidence, 0) > _CONFIDENCE_ORDER.get(existing.confidence, 0):
            best[key] = d
    return list(best.values())

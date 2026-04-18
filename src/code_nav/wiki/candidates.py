"""Wiki 候选筛选 — 复杂度过滤 + stale 检测。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from code_nav.indexer.store import IndexStore


@dataclass
class WikiCandidates:
    new: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    packages: list[dict] = field(default_factory=list)


def compute_candidates(
    store: IndexStore,
    project_root: str,
    wiki_dir: str,
) -> WikiCandidates:
    """计算 wiki 候选列表。

    - new: 无 wiki 且复杂度足够的模块
    - stale: 已有 wiki 但代码更新过的模块
    """
    result = WikiCandidates()
    modules = store.get_all_modules()

    for mod in modules:
        file_rel = mod["file"]
        wiki_path = _module_to_wiki_path(file_rel, wiki_dir)

        if os.path.exists(wiki_path):
            # 已有 wiki → 检查 stale
            code_path = os.path.join(project_root, file_rel)
            if os.path.exists(code_path):
                code_mtime = os.path.getmtime(code_path)
                wiki_mtime = os.path.getmtime(wiki_path)
                if code_mtime > wiki_mtime:
                    result.stale.append({
                        "module": file_rel,
                        "reason": (
                            f"code changed since wiki was written "
                            f"(code: {time.strftime('%Y-%m-%d', time.localtime(code_mtime))}, "
                            f"wiki: {time.strftime('%Y-%m-%d', time.localtime(wiki_mtime))})"
                        ),
                        "priority": "high",
                    })
        else:
            # 跳过测试文件
            if _is_test_file(file_rel):
                continue
            # 跳过 __init__.py（归入 package 级处理）
            if _is_init_file(file_rel):
                continue
            # 无 wiki → 复杂度过滤
            stats = _get_module_stats(store, mod["id"], mod)
            should_generate, priority = _complexity_filter(stats)
            if should_generate:
                result.new.append({
                    "module": file_rel,
                    "reason": _format_reason(stats),
                    "priority": priority,
                })

    # 按优先级排序：high 在前
    priority_order = {"high": 0, "medium": 1}
    result.new.sort(key=lambda x: priority_order.get(x["priority"], 2))
    result.stale.sort(key=lambda x: priority_order.get(x["priority"], 2))

    # Package 候选：每个含 .py 文件的目录都视为 package
    _compute_package_candidates(modules, wiki_dir, project_root, result)

    return result


def _compute_package_candidates(
    modules: list[dict],
    wiki_dir: str,
    project_root: str,
    result: WikiCandidates,
) -> None:
    """收集 package 级别的 wiki 候选。每个含 .py 文件的目录都是一个 package。"""
    # 按 package 分组，统计模块数、最新 mtime、是否有 __init__.py
    pkg_stats: dict[str, dict] = {}  # package -> {count, max_mtime, has_init}
    for mod in modules:
        pkg = mod["package"]
        if not pkg:
            continue
        if pkg not in pkg_stats:
            pkg_stats[pkg] = {"count": 0, "max_mtime": 0.0, "has_init": False}
        pkg_stats[pkg]["count"] += 1
        pkg_stats[pkg]["max_mtime"] = max(
            pkg_stats[pkg]["max_mtime"], mod["mtime"]
        )
        if _is_init_file(mod["file"]):
            pkg_stats[pkg]["has_init"] = True

    priority_order = {"high": 0, "medium": 1}
    new_pkgs: list[dict] = []
    stale_pkgs: list[dict] = []

    for pkg, stats in sorted(pkg_stats.items()):
        wiki_path = _package_to_wiki_path(pkg, wiki_dir)
        if os.path.exists(wiki_path):
            # 已有 _package.md → 检查是否过期
            wiki_mtime = os.path.getmtime(wiki_path)
            if stats["max_mtime"] > wiki_mtime:
                stale_pkgs.append({
                    "package": pkg,
                    "module_count": stats["count"],
                    "has_init": stats["has_init"],
                    "reason": (
                        f"code changed since package wiki was written "
                        f"(latest code: {time.strftime('%Y-%m-%d', time.localtime(stats['max_mtime']))}, "
                        f"wiki: {time.strftime('%Y-%m-%d', time.localtime(wiki_mtime))})"
                    ),
                    "priority": "high",
                })
        else:
            # 跳过纯测试 package
            if _is_test_package(pkg):
                continue
            new_pkgs.append({
                "package": pkg,
                "module_count": stats["count"],
                "has_init": stats["has_init"],
                "reason": f"{stats['count']} modules in package",
                "priority": "high" if stats["count"] >= 3 else "medium",
            })

    new_pkgs.sort(key=lambda x: priority_order.get(x["priority"], 2))
    stale_pkgs.sort(key=lambda x: priority_order.get(x["priority"], 2))

    result.packages = new_pkgs + stale_pkgs


def _package_to_wiki_path(package: str, wiki_dir: str) -> str:
    """package 名 → _package.md 路径。"""
    pkg_path = package.replace(".", os.sep)
    return os.path.join(wiki_dir, pkg_path, "_package.md")


def _is_test_package(package: str) -> bool:
    """判断是否为测试 package。"""
    parts = package.replace(".", "/").split("/")
    for p in parts:
        if p in ("tests", "test"):
            return True
    return False


def _is_init_file(file_rel: str) -> bool:
    """判断是否为 __init__.py 文件。"""
    return file_rel.replace("\\", "/").split("/")[-1] == "__init__.py"


def _is_test_file(file_rel: str) -> bool:
    """判断是否为测试文件。"""
    parts = file_rel.replace("\\", "/").split("/")
    basename = parts[-1]
    # 文件名以 test_ 开头或 _test.py 结尾
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return True
    # conftest.py
    if basename == "conftest.py":
        return True
    # 路径中包含 tests/ 或 test/ 目录
    for p in parts[:-1]:
        if p in ("tests", "test"):
            return True
    return False


def _module_to_wiki_path(file_rel: str, wiki_dir: str) -> str:
    """模块文件 → wiki 文件路径。"""
    wiki_file = file_rel.replace(".py", ".md")
    return os.path.join(wiki_dir, wiki_file)


def _get_module_stats(store: IndexStore, module_id: int, mod: dict) -> dict:
    """获取模块统计信息。"""
    symbol_count = store.conn.execute(
        "SELECT COUNT(*) as cnt FROM symbols WHERE module_id = ?",
        (module_id,),
    ).fetchone()["cnt"]

    dependent_count = store.conn.execute(
        "SELECT COUNT(*) as cnt FROM module_deps WHERE target_module = ?",
        (module_id,),
    ).fetchone()["cnt"]

    return {
        "line_count": mod["line_count"],
        "symbol_count": symbol_count,
        "dependent_module_count": dependent_count,
    }


def _complexity_filter(module_stats: dict) -> tuple[bool, str]:
    """判断无 Wiki 的模块是否值得首次生成，返回 (是否推荐, 优先级)。"""
    line_count = module_stats["line_count"]
    symbol_count = module_stats["symbol_count"]
    dependent_count = module_stats["dependent_module_count"]

    # 跳过：太小的文件
    if line_count < 50 and symbol_count < 3:
        return False, ""

    # 跳过：没有外部依赖方的内部模块
    if dependent_count == 0 and symbol_count < 5:
        return False, ""

    # 高优先级
    if line_count > 300 or dependent_count >= 5:
        return True, "high"

    # 中优先级
    if line_count > 100 or dependent_count >= 2 or symbol_count >= 10:
        return True, "medium"

    return False, ""


def _format_reason(stats: dict) -> str:
    """格式化推荐原因。"""
    parts = []
    if stats["symbol_count"]:
        parts.append(f"{stats['symbol_count']} symbols")
    if stats["dependent_module_count"]:
        parts.append(f"referenced by {stats['dependent_module_count']} modules")
    if stats["line_count"]:
        parts.append(f"{stats['line_count']} lines")
    return ", ".join(parts)

"""Wiki 模板 — _index.md 自动生成。"""
from __future__ import annotations

import json
import os
import time

from code_nav.indexer.store import IndexStore
from code_nav.wiki.candidates import WikiCandidates


def generate_index(
    store: IndexStore,
    wiki_dir: str,
    candidates: WikiCandidates,
) -> str:
    """生成 _index.md 内容。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    modules = store.get_all_modules()

    # 按包分组统计
    pkg_stats: dict[str, dict] = {}
    for mod in modules:
        pkg = mod["package"] or "（根目录）"
        if pkg not in pkg_stats:
            pkg_stats[pkg] = {"count": 0, "symbols": 0}
        pkg_stats[pkg]["count"] += 1
        sym_count = store.conn.execute(
            "SELECT COUNT(*) as cnt FROM symbols WHERE module_id = ?",
            (mod["id"],),
        ).fetchone()["cnt"]
        pkg_stats[pkg]["symbols"] += sym_count

    lines = [
        "# 代码库索引",
        "",
        f"> 自动生成于 {now}，共 {len(modules)} 个 Python 文件",
        "",
        "## 包结构",
        "",
        "| 包 | 模块数 | 总符号数 |",
        "|----|--------|---------|",
    ]
    for pkg, stats in sorted(pkg_stats.items()):
        lines.append(f"| {pkg} | {stats['count']} | {stats['symbols']} |")

    # 推荐生成 Wiki 的 Package
    if candidates.packages:
        new_pkgs = [p for p in candidates.packages if "changed" not in p.get("reason", "")]
        stale_pkgs = [p for p in candidates.packages if "changed" in p.get("reason", "")]
        if new_pkgs:
            lines.extend([
                "",
                "## 推荐生成 Wiki 的 Package",
                "",
                "| 优先级 | Package | 模块数 | `__init__.py` | 原因 |",
                "|--------|---------|--------|---------------|------|",
            ])
            for p in new_pkgs:
                init_mark = "有" if p.get("has_init") else "无"
                lines.append(
                    f"| {p['priority'].upper()} | {p['package']} | {p['module_count']} | {init_mark} | {p['reason']} |"
                )
        if stale_pkgs:
            lines.extend([
                "",
                "## 需要更新的 Package Wiki",
                "",
                "| Package | 模块数 | `__init__.py` | 原因 |",
                "|---------|--------|---------------|------|",
            ])
            for p in stale_pkgs:
                init_mark = "有" if p.get("has_init") else "无"
                lines.append(
                    f"| {p['package']} | {p['module_count']} | {init_mark} | {p['reason']} |"
                )

    # 推荐生成 Wiki 的模块
    if candidates.new:
        lines.extend([
            "",
            "## 推荐生成 Wiki 的模块",
            "",
            "| 优先级 | 模块 | 原因 |",
            "|--------|------|------|",
        ])
        for c in candidates.new:
            lines.append(f"| {c['priority'].upper()} | {c['module']} | {c['reason']} |")

    # 需要更新的 Wiki
    if candidates.stale:
        lines.extend([
            "",
            "## 需要更新的 Wiki",
            "",
            "| 模块 | 原因 |",
            "|------|------|",
        ])
        for c in candidates.stale:
            lines.append(f"| {c['module']} | {c['reason']} |")

    # 全部模块列表
    lines.extend([
        "",
        "## 全部模块列表",
        "",
        "| 模块 | 文件 | 行数 | Wiki |",
        "|------|------|------|------|",
    ])
    for mod in sorted(modules, key=lambda m: m["file"]):
        file_rel = mod["file"]
        if file_rel.replace("\\", "/").split("/")[-1] == "__init__.py":
            # __init__.py 归入 package wiki，不单独生成模块 wiki
            wiki_status = "见 _package.md"
        else:
            wiki_file = file_rel.replace(".py", ".md")
            wiki_path = os.path.join(wiki_dir, wiki_file)
            wiki_status = "[查看](" + wiki_file + ")" if os.path.exists(wiki_path) else "未生成"
        lines.append(
            f"| {file_rel} | {file_rel} | {mod['line_count']} | {wiki_status} |"
        )

    lines.append("")
    return "\n".join(lines)


def write_index(store: IndexStore, wiki_dir: str, candidates: WikiCandidates) -> str:
    """生成并写入 _index.md，返回文件路径。"""
    content = generate_index(store, wiki_dir, candidates)
    index_path = os.path.join(wiki_dir, "_index.md")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(content)
    return index_path

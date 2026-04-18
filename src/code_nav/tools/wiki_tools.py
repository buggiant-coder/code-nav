"""Wiki 工具实现 — build_index, query_symbol, query_module, get_wiki, save_wiki。"""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from code_nav.indexer.store import IndexStore
from code_nav.indexer.builder import CodeIndexer
from code_nav.wiki.manager import WikiManager
from code_nav.wiki.candidates import compute_candidates
from code_nav.wiki.templates import write_index


async def build_index(
    project_path: str,
    analysis_engine=None,
    force: bool = False,
) -> dict:
    """构建/更新代码知识图谱。"""
    project_path = os.path.abspath(project_path)

    code_nav_dir = Path(project_path) / ".code-nav"
    code_nav_dir.mkdir(parents=True, exist_ok=True)
    wiki_dir = str(code_nav_dir / "wiki")

    db_path = str(code_nav_dir / "graph.db")

    store = IndexStore(db_path)
    try:
        store.init_db()

        indexer = CodeIndexer(
            project_path=project_path,
            store=store,
            analysis_engine=analysis_engine,
        )

        result = await indexer.build(force=force)
        result_dict = asdict(result)

        # 计算 wiki_candidates
        candidates = compute_candidates(store, project_path, wiki_dir)
        result_dict["wiki_candidates"] = {
            "new": candidates.new,
            "stale": candidates.stale,
            "packages": candidates.packages,
        }

        # 生成 _index.md
        write_index(store, wiki_dir, candidates)

        return result_dict
    finally:
        store.close()


def _open_store(project_path: str) -> tuple[IndexStore, str]:
    """打开已有的 graph.db，返回 (store, project_path)。"""
    project_path = os.path.abspath(project_path)
    db_path = str(Path(project_path) / ".code-nav" / "graph.db")
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Index not found at {db_path}. Run build_index first."
        )
    store = IndexStore(db_path)
    return store, project_path


async def _ensure_fresh_store(project_path: str) -> tuple[IndexStore, str]:
    """打开 graph.db，若有文件变更则自动执行增量更新（lazy refresh）。"""
    store, project_path = _open_store(project_path)

    indexer = CodeIndexer(project_path=project_path, store=store)
    file_mtimes = indexer._scan_python_files()
    stale = store.get_stale_modules(file_mtimes)

    if stale["changed"] or stale["new"] or stale["deleted"]:
        await indexer.build(force=False)

    return store, project_path


async def query_symbol(
    project_path: str,
    name: str,
    file: str = "",
    include_callers: bool = True,
    include_callees: bool = True,
    max_depth: int = 1,
) -> dict:
    """查询符号详情 + 上下游关系。"""
    store, _ = await _ensure_fresh_store(project_path)
    try:
        result = store.query_symbol(
            name=name,
            file=file or None,
            include_callers=include_callers,
            include_callees=include_callees,
            max_depth=min(max_depth, 3),
        )
        if result is None:
            return {"error": f"Symbol '{name}' not found in index."}
        return result
    finally:
        store.close()


async def query_module(
    project_path: str,
    file: str = "",
    package: str = "",
) -> dict:
    """查询模块概览 — 符号列表 + 依赖关系。"""
    store, project_path = await _ensure_fresh_store(project_path)
    try:
        result = store.query_module(
            file=file or None,
            package=package or None,
        )
        if result is None:
            return {"error": f"Module not found (file={file!r}, package={package!r})."}

        # 用 WikiManager 更新 wiki 状态
        if result and file:
            mgr = WikiManager(project_path)
            result["wiki"] = mgr.get_wiki_status(file)

        return result
    finally:
        store.close()


async def get_wiki(
    project_path: str,
    module: str = "",
    package: str = "",
    level: str = "",
) -> dict:
    """读取 wiki 内容。"""
    project_path = os.path.abspath(project_path)
    mgr = WikiManager(project_path)
    wc = mgr.read(module=module, package=package, level=level)

    result: dict = {
        "level": wc.level,
        "path": wc.path,
        "exists": wc.exists,
    }
    if wc.exists:
        result["content"] = wc.content
        result["last_modified"] = wc.last_modified
        result["is_stale"] = wc.is_stale
        if wc.stale_reason:
            result["stale_reason"] = wc.stale_reason
    else:
        result["content"] = ""
        result["message"] = "No wiki exists yet. Use save_wiki to create one."

    return result


async def save_wiki(
    project_path: str,
    content: str,
    module: str = "",
    package: str = "",
    level: str = "",
) -> dict:
    """保存 wiki 内容。"""
    project_path = os.path.abspath(project_path)
    mgr = WikiManager(project_path)
    rel_path = mgr.write(content=content, module=module, package=package, level=level)

    return {
        "status": "saved",
        "path": rel_path,
    }

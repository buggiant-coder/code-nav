"""CodeIndexer — 全量/增量构建调度，调用 AstParser + JediEngine。"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from code_nav.indexer.ast_parser import AstParser, ParseResult
from code_nav.indexer.store import IndexStore

logger = logging.getLogger(__name__)

EXCLUDED_DIRS = {
    "__pycache__", ".git", ".hg", ".svn",
    ".venv", "venv", ".env", "env",
    "node_modules", ".tox", ".mypy_cache",
    ".pytest_cache", ".code-nav",
    "build", "dist",
}

EXCLUDED_FILES = {
    "setup.py",
}


@dataclass
class BuildResult:
    status: str = "completed"
    mode: str = "full"
    files_scanned: int = 0
    files_updated: int = 0
    files_deleted: int = 0
    symbols_total: int = 0
    edges_total: int = 0
    duration_seconds: float = 0.0


class CodeIndexer:
    """知识图谱构建器 — 全量/增量扫描，构建符号关系图谱。"""

    def __init__(self, project_path: str, store: IndexStore, analysis_engine=None):
        self._project_path = os.path.abspath(project_path)
        self._store = store
        self._analysis_engine = analysis_engine
        self._parser = AstParser()
        # 缓存：file_rel → ParseResult（边构建阶段用）
        self._parse_cache: dict[str, ParseResult] = {}

    async def build(self, force: bool = False) -> BuildResult:
        """构建知识图谱。force=True 全量重建，force=False 增量更新。"""
        start = time.time()
        file_mtimes = self._scan_python_files()

        if force:
            result = await self._full_build(file_mtimes)
        else:
            result = await self._incremental_build(file_mtimes)

        result.duration_seconds = round(time.time() - start, 2)
        return result

    async def _full_build(self, file_mtimes: dict[str, float]) -> BuildResult:
        """全量构建：清空 DB → 逐文件解析 → 构建边 → 聚合依赖。"""
        self._store.clear_all()
        result = BuildResult(mode="full", files_scanned=len(file_mtimes))

        # 1. 逐文件解析并写入 symbols + parameters
        for file_rel, mtime in file_mtimes.items():
            self._process_file(file_rel, mtime)
            result.files_updated += 1
        self._store.commit()

        # 2. 构建边（需要所有符号已入库）
        edge_count = self._build_edges()
        self._store.commit()

        # 3. 聚合 module_deps
        self._store.rebuild_module_deps()
        self._store.commit()

        # 4. 更新 meta
        self._store.set_meta("project_root", self._project_path)
        self._store.set_meta("last_full_build", time.strftime("%Y-%m-%dT%H:%M:%S"))

        result.symbols_total = self._store.get_symbol_count()
        result.edges_total = self._store.get_edge_count()
        return result

    async def _incremental_build(self, file_mtimes: dict[str, float]) -> BuildResult:
        """增量构建：比对 mtime → 处理 changed/new/deleted → 局部重建边。"""
        result = BuildResult(mode="incremental", files_scanned=len(file_mtimes))
        stale = self._store.get_stale_modules(file_mtimes)

        # 处理 deleted
        for f in stale["deleted"]:
            self._store.delete_module(f)
            result.files_deleted += 1

        # 处理 changed + new
        to_process = stale["changed"] + stale["new"]
        for file_rel in to_process:
            # 删除旧数据（changed 的情况）
            module_id = self._store.get_module_id(file_rel)
            if module_id is not None:
                self._store.delete_edges_for_module(module_id)
                # 删除旧的 symbols（CASCADE 会清理 parameters）
                self._store.conn.execute(
                    "DELETE FROM symbols WHERE module_id = ?", (module_id,)
                )
            mtime = file_mtimes[file_rel]
            self._process_file(file_rel, mtime)
            result.files_updated += 1
        self._store.commit()

        if to_process or stale["deleted"]:
            # 重建所有边：需要先把未变更文件也解析到 cache 中
            for file_rel in file_mtimes:
                if file_rel not in self._parse_cache:
                    full_path = os.path.join(self._project_path, file_rel)
                    pr = self._parser.parse_file(full_path)
                    if pr is not None:
                        module_name = self._file_to_module_name(file_rel)
                        for sym in pr.symbols:
                            old_prefix = Path(file_rel).stem + "."
                            if sym.qualified_name.startswith(old_prefix):
                                sym.qualified_name = module_name + "." + sym.qualified_name[len(old_prefix):]
                            elif not sym.qualified_name.startswith(module_name):
                                sym.qualified_name = module_name + "." + sym.name
                        self._parse_cache[file_rel] = pr

            self._store.conn.execute("DELETE FROM edges")
            self._build_edges()
            self._store.commit()

            self._store.rebuild_module_deps()
            self._store.commit()

        result.symbols_total = self._store.get_symbol_count()
        result.edges_total = self._store.get_edge_count()
        return result

    # ================================================================
    # 文件扫描
    # ================================================================

    def _scan_python_files(self) -> dict[str, float]:
        """扫描项目下所有 .py 文件，返回 {relative_path: mtime}。"""
        result = {}
        root = Path(self._project_path)
        for dirpath, dirnames, filenames in os.walk(root):
            # 过滤排除目录（就地修改 dirnames 以跳过子目录遍历）
            dirnames[:] = [
                d for d in dirnames
                if d not in EXCLUDED_DIRS
                and not d.startswith(".")
                and not d.endswith(".egg-info")
            ]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                if filename in EXCLUDED_FILES:
                    continue
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, self._project_path)
                # 统一使用 / 分隔符
                rel_path = rel_path.replace(os.sep, "/")
                result[rel_path] = os.path.getmtime(full_path)
        return result

    # ================================================================
    # 单文件处理
    # ================================================================

    def _process_file(self, file_rel: str, mtime: float) -> None:
        """解析单文件，写入 module + symbols + parameters。"""
        full_path = os.path.join(self._project_path, file_rel)
        parse_result = self._parser.parse_file(full_path)
        if parse_result is None:
            return  # 解析失败，跳过

        # 缓存 ParseResult 用于后续边构建
        self._parse_cache[file_rel] = parse_result

        package = self._determine_package(file_rel)
        module_name = self._file_to_module_name(file_rel)

        # 更新 qualified_name 使用完整模块名
        for sym in parse_result.symbols:
            # ast_parser 使用了简单的 stem 作为 module_name
            # 这里用完整的 module_name 替换
            old_prefix = Path(file_rel).stem + "."
            if sym.qualified_name.startswith(old_prefix):
                sym.qualified_name = module_name + "." + sym.qualified_name[len(old_prefix):]
            elif not sym.qualified_name.startswith(module_name):
                sym.qualified_name = module_name + "." + sym.name

        module_id = self._store.upsert_module(
            file=file_rel,
            package=package,
            mtime=mtime,
            line_count=parse_result.line_count,
        )

        # 批量插入符号
        sym_dicts = []
        for sym in parse_result.symbols:
            sym_dicts.append({
                "name": sym.name,
                "qualified_name": sym.qualified_name,
                "symbol_type": sym.symbol_type,
                "scope": sym.scope,
                "line": sym.line,
                "end_line": sym.end_line,
                "column": sym.column,
                "signature": sym.signature,
                "return_type": sym.return_type,
                "docstring": sym.docstring,
                "decorators": sym.decorators,
                "parent_name": sym.parent_name,
            })

        qname_to_id = self._store.batch_insert_symbols(module_id, sym_dicts)

        # 解析 parent_id
        self._store.resolve_parent_ids(qname_to_id, sym_dicts)

        # 插入参数
        for sym, d in zip(parse_result.symbols, sym_dicts):
            if sym.parameters:
                sid = qname_to_id.get(d["qualified_name"])
                if sid:
                    self._store.insert_parameters(sid, [
                        {
                            "name": p.name,
                            "position": p.position,
                            "type_annotation": p.type_annotation,
                            "default_value": p.default_value,
                            "kind": p.kind,
                        }
                        for p in sym.parameters
                    ])

    def _determine_package(self, file_rel: str) -> str | None:
        """从相对路径推断 package 名。"""
        parts = Path(file_rel).parts
        if len(parts) <= 1:
            return None  # 根目录文件，无 package
        # 取目录路径作为 package（用 . 分隔）
        return ".".join(parts[:-1])

    def _file_to_module_name(self, file_rel: str) -> str:
        """将文件相对路径转为模块名。

        e.g. "util/odps_util.py" → "util.odps_util"
             "order.py" → "order"
             "__init__.py" → "__init__"
             "util/__init__.py" → "util"
        """
        p = Path(file_rel)
        if p.name == "__init__.py":
            if len(p.parts) > 1:
                return ".".join(p.parts[:-1])
            return "__init__"
        return ".".join(p.with_suffix("").parts)

    # ================================================================
    # 边构建
    # ================================================================

    def _build_edges(self) -> int:
        """构建跨文件边（imports, calls, inherits）。"""
        edges: list[dict] = []

        edges.extend(self._build_import_edges())
        edges.extend(self._build_call_edges())
        edges.extend(self._build_inherits_edges())

        if edges:
            self._store.insert_edges(edges)

        return len(edges)

    def _build_import_edges(self) -> list[dict]:
        """从 AstParser 的 import 信息构建 import 边。"""
        edges = []
        for file_rel, parse_result in self._parse_cache.items():
            module_id = self._store.get_module_id(file_rel)
            if module_id is None:
                continue

            for imp in parse_result.imports:
                if not imp.names:
                    # import module 形式（不是 from...import）
                    continue

                for name in imp.names:
                    if name == "*":
                        continue

                    # 尝试在已索引的符号中找到 target
                    # 先尝试 module.name 的全限定名
                    target_id = self._store.find_symbol_by_qualified_name(
                        f"{imp.module}.{name}"
                    )
                    if target_id is None:
                        # 退一步：直接按 name 查找
                        target_id = self._store.find_symbol_id(name)

                    if target_id is None:
                        continue  # 外部依赖或标准库，跳过

                    # 找到当前模块中使用这个 import 的符号作为 source
                    # 简单处理：如果当前模块有符号，用模块中第一个符号
                    # 更精确：创建一个虚拟的 "module-level" source
                    # 这里用模块中引用该 name 的符号，或者用模块的第一个定义
                    source_id = self._find_importer_symbol(file_rel, module_id)
                    if source_id is None:
                        continue

                    edges.append({
                        "source_id": source_id,
                        "target_id": target_id,
                        "edge_type": "imports",
                        "file": file_rel,
                        "line": imp.line,
                    })

        return edges

    def _find_importer_symbol(self, file_rel: str, module_id: int) -> int | None:
        """找到模块中适合作为 import 边 source 的符号。

        优先返回模块级函数/类，如果没有则返回任意符号。
        """
        row = self._store.conn.execute(
            """SELECT id FROM symbols
               WHERE module_id = ? AND parent_id IS NULL
               ORDER BY line ASC LIMIT 1""",
            (module_id,),
        ).fetchone()
        if row:
            return row["id"]
        # fallback
        row = self._store.conn.execute(
            "SELECT id FROM symbols WHERE module_id = ? LIMIT 1",
            (module_id,),
        ).fetchone()
        return row["id"] if row else None

    def _build_call_edges(self) -> list[dict]:
        """构建函数调用边 — 分析函数体中引用了哪些其他函数/类。"""
        edges = []
        # 遍历所有函数/方法符号
        rows = self._store.conn.execute(
            """SELECT s.id, s.name, s.qualified_name, s.line, s.end_line,
                      s.symbol_type, m.file
               FROM symbols s
               JOIN modules m ON s.module_id = m.id
               WHERE s.symbol_type IN ('function', 'method')"""
        ).fetchall()

        for row in rows:
            if row["end_line"] is None:
                continue
            file_rel = row["file"]
            full_path = os.path.join(self._project_path, file_rel)
            if not os.path.exists(full_path):
                continue

            # 在函数体范围内查找引用的符号
            call_targets = self._find_calls_in_range(
                full_path, row["line"], row["end_line"], row["id"]
            )
            edges.extend(call_targets)

        return edges

    def _find_calls_in_range(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        source_id: int,
    ) -> list[dict]:
        """在函数体范围内查找调用的其他符号。

        使用简单的 AST 分析找到 Name/Attribute 节点，
        然后在 symbols 表中查找匹配的符号。
        """
        import ast as _ast

        edges = []
        try:
            source = Path(file_path).read_text(encoding="utf-8")
            tree = _ast.parse(source)
        except (SyntaxError, OSError, UnicodeDecodeError):
            return edges

        # 收集函数体内引用的名称
        called_names: set[tuple[str, int]] = set()  # (name, line)
        for node in _ast.walk(tree):
            if not hasattr(node, "lineno"):
                continue
            if node.lineno < start_line or node.lineno > end_line:
                continue

            if isinstance(node, _ast.Call):
                if isinstance(node.func, _ast.Name):
                    called_names.add((node.func.id, node.lineno))
                elif isinstance(node.func, _ast.Attribute):
                    called_names.add((node.func.attr, node.lineno))

        # 在 symbols 表中查找匹配
        file_rel = os.path.relpath(file_path, self._project_path).replace(os.sep, "/")
        for name, line in called_names:
            target_id = self._store.find_symbol_id(name)
            if target_id is not None and target_id != source_id:
                edges.append({
                    "source_id": source_id,
                    "target_id": target_id,
                    "edge_type": "calls",
                    "file": file_rel,
                    "line": line,
                })

        return edges

    def _build_inherits_edges(self) -> list[dict]:
        """从 ClassDef.bases 构建继承边。"""
        edges = []
        # 查找所有 class 符号，signature 中包含基类信息
        rows = self._store.conn.execute(
            """SELECT s.id, s.name, s.qualified_name, s.signature, m.file
               FROM symbols s
               JOIN modules m ON s.module_id = m.id
               WHERE s.symbol_type = 'class'"""
        ).fetchall()

        for row in rows:
            sig = row["signature"]
            if "(" not in sig:
                continue
            # 从 "class Foo(Bar, Baz)" 提取基类
            bases_str = sig.split("(", 1)[1].rstrip(")")
            if not bases_str:
                continue
            base_names = [b.strip() for b in bases_str.split(",")]
            for base in base_names:
                if not base or base in ("object",):
                    continue
                # 查找基类
                target_id = self._store.find_symbol_id(base)
                if target_id is not None:
                    edges.append({
                        "source_id": row["id"],
                        "target_id": target_id,
                        "edge_type": "inherits",
                        "file": row["file"],
                    })

        return edges

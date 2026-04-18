"""IndexStore — SQLite 读写封装，管理知识图谱数据库。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class IndexStore:
    """知识图谱 SQLite 数据库的读写封装。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ================================================================
    # 初始化
    # ================================================================

    def init_db(self) -> None:
        """读取 schema.sql 建表，写入 index_meta 初始记录。"""
        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)
        # 初始 meta（仅在不存在时插入）
        self.conn.execute(
            "INSERT OR IGNORE INTO index_meta (key, value) VALUES (?, ?)",
            ("version", "1"),
        )
        self.conn.commit()

    def clear_all(self) -> None:
        """清空全部数据（全量重建时用）。"""
        for table in ("module_deps", "edges", "parameters", "symbols", "modules"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    # ================================================================
    # Module CRUD
    # ================================================================

    def upsert_module(
        self,
        file: str,
        package: str | None,
        mtime: float,
        line_count: int,
    ) -> int:
        """插入或更新模块，返回 module_id。"""
        cur = self.conn.execute(
            """INSERT INTO modules (file, package, mtime, last_indexed, line_count)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(file) DO UPDATE SET
                   package = excluded.package,
                   mtime = excluded.mtime,
                   last_indexed = excluded.last_indexed,
                   line_count = excluded.line_count""",
            (file, package, mtime, time.time(), line_count),
        )
        # 获取 id（不论是 insert 还是 update）
        row = self.conn.execute(
            "SELECT id FROM modules WHERE file = ?", (file,)
        ).fetchone()
        return row["id"]

    def delete_module(self, file: str) -> None:
        """删除模块及其所有 symbols, params, edges（CASCADE）。"""
        self.conn.execute("DELETE FROM modules WHERE file = ?", (file,))

    def get_all_modules(self) -> list[dict]:
        """返回所有模块信息。"""
        rows = self.conn.execute("SELECT * FROM modules").fetchall()
        return [dict(r) for r in rows]

    def get_module_id(self, file: str) -> int | None:
        """按文件路径查找 module_id。"""
        row = self.conn.execute(
            "SELECT id FROM modules WHERE file = ?", (file,)
        ).fetchone()
        return row["id"] if row else None

    def get_stale_modules(
        self, file_mtimes: dict[str, float]
    ) -> dict[str, list[str]]:
        """比对 mtime，返回 {changed: [...], new: [...], deleted: [...]}。"""
        existing = {}
        for row in self.conn.execute("SELECT file, mtime FROM modules"):
            existing[row["file"]] = row["mtime"]

        changed = []
        new = []
        deleted = []

        # 检查已有模块
        for f, old_mtime in existing.items():
            if f not in file_mtimes:
                deleted.append(f)
            elif file_mtimes[f] != old_mtime:
                changed.append(f)

        # 检查新文件
        for f in file_mtimes:
            if f not in existing:
                new.append(f)

        return {"changed": changed, "new": new, "deleted": deleted}

    # ================================================================
    # Symbol CRUD
    # ================================================================

    def upsert_symbol(self, module_id: int, **kwargs) -> int:
        """插入符号，返回 symbol_id。"""
        cols = ["module_id"]
        vals: list = [module_id]
        for k in (
            "name", "qualified_name", "symbol_type", "scope",
            "line", "end_line", "column", "signature", "return_type",
            "docstring", "decorators", "parent_id",
        ):
            if k in kwargs:
                cols.append(k)
                v = kwargs[k]
                if k == "decorators" and isinstance(v, list):
                    v = json.dumps(v)
                vals.append(v)

        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        cur = self.conn.execute(
            f"INSERT INTO symbols ({col_names}) VALUES ({placeholders})",
            vals,
        )
        return cur.lastrowid  # type: ignore[return-value]

    def batch_insert_symbols(
        self, module_id: int, symbols: list[dict]
    ) -> dict[str, int]:
        """批量插入符号，返回 {qualified_name: symbol_id} 映射。"""
        result: dict[str, int] = {}
        for sym in symbols:
            sid = self.upsert_symbol(module_id, **sym)
            result[sym["qualified_name"]] = sid
        return result

    def resolve_parent_ids(self, qname_to_id: dict[str, int], symbols: list[dict]) -> None:
        """根据 parent_name 设置 parent_id。在 batch_insert_symbols 之后调用。"""
        for sym in symbols:
            parent_name = sym.get("parent_name")
            if not parent_name:
                continue
            # 查找 parent 的 qualified_name
            # parent_name 可能是 "module.ClassName"
            sid = qname_to_id.get(sym["qualified_name"])
            if sid is None:
                continue
            # 尝试在同一批次中找到 parent
            parent_qname = None
            for pq, pid in qname_to_id.items():
                if pq.endswith(f".{parent_name}") or pq == parent_name:
                    parent_qname = pq
                    break
            if parent_qname and parent_qname in qname_to_id:
                parent_id = qname_to_id[parent_qname]
                self.conn.execute(
                    "UPDATE symbols SET parent_id = ? WHERE id = ?",
                    (parent_id, sid),
                )

    # ================================================================
    # Parameters
    # ================================================================

    def insert_parameters(self, symbol_id: int, params: list[dict]) -> None:
        """批量插入参数。"""
        for p in params:
            self.conn.execute(
                """INSERT INTO parameters
                   (symbol_id, name, position, type_annotation, default_value, kind)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    symbol_id,
                    p["name"],
                    p["position"],
                    p.get("type_annotation", ""),
                    p.get("default_value", ""),
                    p.get("kind", "POSITIONAL_OR_KEYWORD"),
                ),
            )

    # ================================================================
    # Edges
    # ================================================================

    def insert_edges(self, edges: list[dict]) -> None:
        """批量插入边（忽略重复）。"""
        for e in edges:
            self.conn.execute(
                """INSERT OR IGNORE INTO edges
                   (source_id, target_id, edge_type, file, line)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    e["source_id"],
                    e["target_id"],
                    e["edge_type"],
                    e.get("file"),
                    e.get("line"),
                ),
            )

    def delete_edges_for_module(self, module_id: int) -> None:
        """删除某模块所有符号的 outgoing edges。"""
        self.conn.execute(
            """DELETE FROM edges WHERE source_id IN
               (SELECT id FROM symbols WHERE module_id = ?)""",
            (module_id,),
        )

    # ================================================================
    # Module Deps
    # ================================================================

    def rebuild_module_deps(self) -> None:
        """从 edges 表聚合生成 module_deps。"""
        self.conn.execute("DELETE FROM module_deps")
        self.conn.execute(
            """INSERT OR IGNORE INTO module_deps (source_module, target_module, import_names)
               SELECT
                   s_sym.module_id,
                   t_sym.module_id,
                   '[]'
               FROM edges e
               JOIN symbols s_sym ON e.source_id = s_sym.id
               JOIN symbols t_sym ON e.target_id = t_sym.id
               WHERE s_sym.module_id != t_sym.module_id
               GROUP BY s_sym.module_id, t_sym.module_id"""
        )
        # 更新 import_names：收集每对模块之间的 import 边的 target 符号名
        rows = self.conn.execute(
            """SELECT md.id, md.source_module, md.target_module
               FROM module_deps md"""
        ).fetchall()
        for row in rows:
            names_rows = self.conn.execute(
                """SELECT DISTINCT t_sym.name
                   FROM edges e
                   JOIN symbols s_sym ON e.source_id = s_sym.id
                   JOIN symbols t_sym ON e.target_id = t_sym.id
                   WHERE s_sym.module_id = ? AND t_sym.module_id = ?
                     AND e.edge_type = 'imports'""",
                (row["source_module"], row["target_module"]),
            ).fetchall()
            names = [r["name"] for r in names_rows]
            if names:
                self.conn.execute(
                    "UPDATE module_deps SET import_names = ? WHERE id = ?",
                    (json.dumps(names), row["id"]),
                )

    # ================================================================
    # Meta
    # ================================================================

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM index_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    # ================================================================
    # Lookup helpers
    # ================================================================

    def find_symbol_by_qualified_name(self, qualified_name: str) -> int | None:
        """按全限定名查找 symbol_id。"""
        row = self.conn.execute(
            "SELECT id FROM symbols WHERE qualified_name = ?",
            (qualified_name,),
        ).fetchone()
        return row["id"] if row else None

    def find_symbol_id(self, name: str, module_file: str | None = None) -> int | None:
        """按名称查找 symbol_id（module_file 用于消歧）。"""
        if module_file:
            row = self.conn.execute(
                """SELECT s.id FROM symbols s
                   JOIN modules m ON s.module_id = m.id
                   WHERE s.name = ? AND m.file = ?""",
                (name, module_file),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT id FROM symbols WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
        return row["id"] if row else None

    # ================================================================
    # 查询方法 (Phase 7)
    # ================================================================

    def query_symbol(
        self,
        name: str,
        file: str | None = None,
        include_callers: bool = True,
        include_callees: bool = True,
        max_depth: int = 1,
    ) -> dict | None:
        """查询符号完整信息 + 上下游关系。"""
        # 查找符号
        if file:
            row = self.conn.execute(
                """SELECT s.*, m.file, m.package
                   FROM symbols s JOIN modules m ON s.module_id = m.id
                   WHERE s.name = ? AND m.file = ?""",
                (name, file),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT s.*, m.file, m.package
                   FROM symbols s JOIN modules m ON s.module_id = m.id
                   WHERE s.name = ?""",
                (name,),
            ).fetchone()
        if not row:
            return None

        symbol_id = row["id"]
        symbol = {
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "type": row["symbol_type"],
            "file": row["file"],
            "line": row["line"],
            "end_line": row["end_line"],
            "scope": row["scope"],
            "signature": row["signature"],
            "return_type": row["return_type"],
            "docstring": row["docstring"],
            "decorators": json.loads(row["decorators"]) if row["decorators"] else [],
        }

        # 参数列表
        params = self.conn.execute(
            """SELECT name, type_annotation, position, default_value, kind
               FROM parameters WHERE symbol_id = ? ORDER BY position""",
            (symbol_id,),
        ).fetchall()
        symbol["parameters"] = [
            {
                "name": p["name"],
                "type": p["type_annotation"],
                "position": p["position"],
                "default": p["default_value"],
                "kind": p["kind"],
            }
            for p in params
        ]

        result: dict = {"symbol": symbol}

        # callers
        if include_callers:
            result["callers"] = self._get_related_symbols(
                symbol_id, direction="callers", max_depth=max_depth
            )

        # callees
        if include_callees:
            result["callees"] = self._get_related_symbols(
                symbol_id, direction="callees", max_depth=max_depth
            )

        # index freshness
        meta = self.get_meta("last_full_build")
        if meta:
            result["index_freshness"] = meta

        return result

    def _get_related_symbols(
        self, symbol_id: int, direction: str, max_depth: int
    ) -> list[dict]:
        """获取符号的上游（callers）或下游（callees）关系。"""
        results = []
        visited: set[int] = {symbol_id}
        current_ids = [symbol_id]

        for depth in range(max_depth):
            if not current_ids:
                break
            next_ids = []
            for sid in current_ids:
                if direction == "callers":
                    rows = self.conn.execute(
                        """SELECT e.edge_type, e.file, e.line,
                                  s.name, s.qualified_name, s.symbol_type,
                                  s.id as sym_id, m.file as sym_file
                           FROM edges e
                           JOIN symbols s ON e.source_id = s.id
                           JOIN modules m ON s.module_id = m.id
                           WHERE e.target_id = ?""",
                        (sid,),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        """SELECT e.edge_type, e.file, e.line,
                                  s.name, s.qualified_name, s.symbol_type,
                                  s.id as sym_id, m.file as sym_file
                           FROM edges e
                           JOIN symbols s ON e.target_id = s.id
                           JOIN modules m ON s.module_id = m.id
                           WHERE e.source_id = ?""",
                        (sid,),
                    ).fetchall()

                for r in rows:
                    if r["sym_id"] in visited:
                        continue
                    visited.add(r["sym_id"])
                    results.append({
                        "symbol": r["qualified_name"],
                        "name": r["name"],
                        "type": r["symbol_type"],
                        "file": r["sym_file"],
                        "line": r["line"],
                        "edge_type": r["edge_type"],
                        "depth": depth + 1,
                    })
                    next_ids.append(r["sym_id"])
            current_ids = next_ids

        return results

    @staticmethod
    def _calc_suggested_wiki_lines(line_count: int) -> int:
        """根据源码行数计算建议 wiki 行数（压缩比随规模递增）。"""
        if line_count < 100:
            ratio = 3
        elif line_count < 300:
            ratio = 5
        elif line_count < 600:
            ratio = 8
        else:
            ratio = 12
        return max(15, line_count // ratio)

    def query_module(
        self,
        file: str | None = None,
        package: str | None = None,
    ) -> dict | None:
        """查询模块概览 — 模块内符号 + 依赖关系。"""
        if file:
            mod = self.conn.execute(
                "SELECT * FROM modules WHERE file = ?", (file,)
            ).fetchone()
        elif package:
            # package → file：尝试 package.replace(".", "/") + ".py" 或 __init__.py
            mod = self.conn.execute(
                "SELECT * FROM modules WHERE package = ?", (package,)
            ).fetchone()
            if not mod:
                # 尝试直接匹配文件
                candidate = package.replace(".", "/") + ".py"
                mod = self.conn.execute(
                    "SELECT * FROM modules WHERE file = ?", (candidate,)
                ).fetchone()
        else:
            return None

        if not mod:
            return None

        module_id = mod["id"]
        module_info = {
            "file": mod["file"],
            "package": mod["package"],
            "line_count": mod["line_count"],
            "last_indexed": mod["last_indexed"],
        }

        # 符号列表
        syms = self.conn.execute(
            """SELECT id, name, qualified_name, symbol_type, scope,
                      line, end_line, signature, parent_id
               FROM symbols WHERE module_id = ? ORDER BY line""",
            (module_id,),
        ).fetchall()

        symbols = []
        for s in syms:
            sym_info: dict = {
                "name": s["name"],
                "type": s["symbol_type"],
                "line": s["line"],
                "scope": s["scope"],
                "signature": s["signature"],
            }

            # caller/callee 计数
            caller_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM edges WHERE target_id = ?",
                (s["id"],),
            ).fetchone()["cnt"]
            callee_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM edges WHERE source_id = ?",
                (s["id"],),
            ).fetchone()["cnt"]
            sym_info["caller_count"] = caller_count
            sym_info["callee_count"] = callee_count

            # 类的方法列表
            if s["symbol_type"] == "class":
                methods = self.conn.execute(
                    """SELECT name FROM symbols
                       WHERE parent_id = ? AND symbol_type = 'method'
                       ORDER BY line""",
                    (s["id"],),
                ).fetchall()
                sym_info["methods"] = [m["name"] for m in methods]

            symbols.append(sym_info)

        # 依赖（本模块依赖谁）
        deps = self.conn.execute(
            """SELECT m.file as module, md.import_names
               FROM module_deps md
               JOIN modules m ON md.target_module = m.id
               WHERE md.source_module = ?""",
            (module_id,),
        ).fetchall()
        dependencies = [
            {"module": d["module"], "imports": json.loads(d["import_names"])}
            for d in deps
        ]

        # 被依赖（谁依赖本模块）
        rev_deps = self.conn.execute(
            """SELECT m.file as module, md.import_names
               FROM module_deps md
               JOIN modules m ON md.source_module = m.id
               WHERE md.target_module = ?""",
            (module_id,),
        ).fetchall()
        dependents = [
            {"module": d["module"], "imports": json.loads(d["import_names"])}
            for d in rev_deps
        ]

        return {
            "module": module_info,
            "symbols": symbols,
            "dependencies": dependencies,
            "dependents": dependents,
            "suggested_wiki_lines": self._calc_suggested_wiki_lines(mod["line_count"]),
            "wiki": {"status": "not_generated"},
        }

    def get_symbol_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM symbols").fetchone()
        return row["cnt"]

    def get_edge_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM edges").fetchone()
        return row["cnt"]

    def commit(self) -> None:
        self.conn.commit()

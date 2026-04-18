-- code-nav 知识图谱 SQLite Schema
-- 6 张表：index_meta, modules, symbols, parameters, edges, module_deps

-- 元信息
CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 模块（每个 .py 文件 = 一个模块）
CREATE TABLE IF NOT EXISTS modules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file         TEXT    NOT NULL UNIQUE,  -- 相对于 project_root 的路径
    package      TEXT,                     -- 所属包名，如 'util', 'scene.online_service'
    mtime        REAL    NOT NULL,         -- 文件 mtime（用于增量更新判断）
    last_indexed REAL    NOT NULL,         -- 上次索引时间
    line_count   INTEGER DEFAULT 0         -- 文件总行数
);

-- 符号（函数、类、方法、变量、常量）
CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id      INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    name           TEXT    NOT NULL,         -- 符号名，如 'calculate_total'
    qualified_name TEXT    NOT NULL,         -- 全限定名，如 'order.OrderProcessor.process'
    symbol_type    TEXT    NOT NULL,         -- function | class | method | variable
    scope          TEXT    NOT NULL,         -- public | private | module_private
    line           INTEGER NOT NULL,
    end_line       INTEGER,                  -- 符号结束行（函数/类体结束位置）
    column         INTEGER NOT NULL DEFAULT 0,
    signature      TEXT    DEFAULT '',       -- 完整签名字符串
    return_type    TEXT    DEFAULT '',       -- 返回类型注解
    docstring      TEXT    DEFAULT '',       -- docstring
    decorators     TEXT    DEFAULT '',       -- 装饰器列表，JSON array
    parent_id      INTEGER REFERENCES symbols(id) ON DELETE CASCADE
    -- parent_id: 方法→所属类，嵌套函数→外层函数
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_module ON symbols(module_id);
CREATE INDEX IF NOT EXISTS idx_symbols_type ON symbols(symbol_type);

-- 参数（函数/方法的参数，到参数级粒度）
CREATE TABLE IF NOT EXISTS parameters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id       INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    position        INTEGER NOT NULL,         -- 参数位置（0-based）
    type_annotation TEXT    DEFAULT '',       -- 类型注解
    default_value   TEXT    DEFAULT '',       -- 默认值（源码字符串）
    kind            TEXT    DEFAULT 'POSITIONAL_OR_KEYWORD'
    -- kind: POSITIONAL_ONLY | POSITIONAL_OR_KEYWORD | VAR_POSITIONAL | KEYWORD_ONLY | VAR_KEYWORD
);

CREATE INDEX IF NOT EXISTS idx_params_symbol ON parameters(symbol_id);

-- 边（符号之间的关系）
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    edge_type   TEXT    NOT NULL,   -- calls | imports | inherits | uses | overrides
    file        TEXT,               -- 引用发生的文件
    line        INTEGER,            -- 引用发生的行号
    UNIQUE(source_id, target_id, edge_type, line)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);

-- 模块间依赖（模块级聚合，加速 query_module）
CREATE TABLE IF NOT EXISTS module_deps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_module INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    target_module INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    import_names  TEXT    DEFAULT '',  -- 导入的符号名列表，JSON array
    UNIQUE(source_module, target_module)
);

CREATE INDEX IF NOT EXISTS idx_module_deps_source ON module_deps(source_module);
CREATE INDEX IF NOT EXISTS idx_module_deps_target ON module_deps(target_module);

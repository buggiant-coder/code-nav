# 架构设计

## 1. 设计目标

为 AI 编程 Agent 提供**语义级代码导航能力**，替代当前基于 Grep 的纯文本搜索方案。

核心设计原则：

1. **面向工作流** — 不仅暴露原子能力（find_references），更提供贴合编程工作流的高级工具（pre_change_analysis）
2. **多引擎互补** — Pyright LSP 提供类型感知的精确分析，ast-grep 提供跨语言的结构搜索，两者结果合并去重
3. **MCP 标准协议** — 一次实现，所有支持 MCP 的 Agent 均可接入
4. **低侵入** — 不修改用户项目代码，不需要特殊构建步骤

## 2. 整体架构

```
┌──────────────────────────────────────────────────────┐
│                    MCP Clients                       │
│  Claude Code  │  Cursor  │  Continue  │  自建 Agent   │
└──────┬────────┴─────┬────┴──────┬─────┴──────┬───────┘
       │              │          │            │
       └──────────────┴──────────┴────────────┘
                      │ MCP Protocol (stdio)
                      ▼
┌──────────────────────────────────────────────────────┐
│              MCP Server: code-nav                    │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │           Wiki / 索引层                         │  │
│  │                                                │  │
│  │  build_index │ query_symbol │ query_module     │  │
│  │  get_wiki    │ save_wiki                       │  │
│  └───────────────┬────────────────────────────────┘  │
│                  │                                    │
│  ┌────────────────────────────────────────────────┐  │
│  │           高级工具层 (Workflow Tools)            │  │
│  │                                                │  │
│  │  get_change_scope │ pre_change_analysis        │  │
│  │  post_change_validate                          │  │
│  └───────────────┬────────────────────────────────┘  │
│                  │ 内部组合调用                        │
│  ┌────────────────────────────────────────────────┐  │
│  │           低级工具层 (Primitive Tools)           │  │
│  │                                                │  │
│  │  find_references │ go_to_definition            │  │
│  │  ast_search      │ check_diagnostics           │  │
│  └───────────────┬────────────────────────────────┘  │
│                  │ 调用                              │
│  ┌────────────────────────────────────────────────┐  │
│  │           引擎层 (Analysis Engines)             │  │
│  │                                                │  │
│  │  ┌────────────────┐  ┌───────────────────┐     │  │
│  │  │ pyright engine │  │  ast-grep engine  │     │  │
│  │  │ (Python, LSP)  │  │  (All languages)  │     │  │
│  │  └────────────────┘  └───────────────────┘     │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │           知识图谱 (Knowledge Graph)            │  │
│  │                                                │  │
│  │  indexer/ast_parser  (AST 解析，符号提取)        │  │
│  │  indexer/builder     (全量/增量构建调度)          │  │
│  │  indexer/store       (SQLite 持久化)             │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │           Wiki 系统                             │  │
│  │                                                │  │
│  │  wiki/manager     (三级读写、路径映射、过期检测)   │  │
│  │  wiki/candidates  (候选计算)                     │  │
│  │  wiki/templates   (索引页生成)                    │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │           基础设施层                             │  │
│  │  结果合并去重 │ 分析结果缓存 │ 项目/测试文件发现   │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

## 3. 分层说明

### 3.1 引擎层 (Analysis Engines)

负责实际的代码分析，每个引擎封装一个底层工具：

#### pyright engine

- **职责**: Python 语义分析（引用查找、定义跳转、类型推断、签名提取）
- **实现**: 通过 `pyright-langserver --stdio` 子进程，使用 JSON-RPC 通信
- **优势**: 类型感知，支持跨继承引用查找、re-export 穿透、完整类型推断
- **局限**: 仅支持 Python；需要管理 LSP 子进程生命周期
- **接口**: 接收 `(file, line, col)` → 返回 `List[Location]`

#### ast-grep engine

- **职责**: 基于 AST 的结构模式匹配搜索
- **优势**: 支持几十种语言；即时搜索无需预索引；模式表达力强
- **局限**: 不理解类型系统，可能有误报
- **接口**: 接收 `(pattern, language, path)` → 返回 `List[Match]`

### 3.2 低级工具层 (Primitive Tools)

将引擎能力封装为标准 MCP 工具，处理引擎选择和结果合并：

- **find_references**: 根据文件类型选择引擎（Python → pyright + ast-grep 并行），合并去重后返回
- **go_to_definition**: 同上
- **ast_search**: 直接使用 ast-grep 引擎
- **check_diagnostics**: 通过 `pyright --outputjson` CLI 执行类型检查，返回诊断信息（类型错误、警告等）

### 3.3 高级工具层 (Workflow Tools)

组合低级工具，面向编程工作流提供端到端能力：

- **get_change_scope**: 快速风险评估，轻量级调用
- **pre_change_analysis**: 改前影响分析，组合 find_references + 签名提取 + 测试文件关联
- **post_change_validate**: 改后验证，组合 find_references + 签名对比

修改完成后，还可以调用 `check_diagnostics`（低级工具层）运行类型检查，获取 IDE 级别的错误提示。

### 3.4 知识图谱 (Knowledge Graph)

基于 SQLite 的代码知识图谱，持久化存储符号及其关系：

- **ast_parser**: 使用 Python AST 解析源文件，提取符号（函数、类、方法、变量）、参数、导入信息
- **builder**: 调度全量/增量构建 — 扫描 `.py` 文件 → 解析符号 → 构建跨文件边（imports, calls, inherits） → 聚合模块依赖
- **store**: SQLite 存储层，管理 modules、symbols、parameters、edges、module_deps 五张表

增量构建通过 mtime 比对检测变更文件，仅重新解析 changed/new/deleted 文件。查询工具（`query_symbol`、`query_module`）内置 lazy refresh 机制，自动检测文件变更并触发增量更新。

### 3.5 Wiki 系统

三级 Wiki 文档系统（项目 → 包 → 模块），帮助 Agent 快速理解代码库业务逻辑：

- **manager**: 路径映射（模块文件 → wiki 文件）、读写、过期检测（基于 mtime 实时比对）
- **candidates**: 计算需要新建或更新 wiki 的模块列表
- **templates**: 生成 `_index.md` 索引页

设计原则：`__init__.py` 不单独生成模块级 Wiki，其公共 API 由所属包的 `_package.md` 承载。

### 3.6 基础设施层

- **结果合并去重** (`utils/merge.py`): 多引擎结果的去重和置信度标注
- **分析结果缓存** (`utils/cache.py`): 短期缓存，避免重复计算
- **项目/测试文件发现** (`utils/project.py`): 项目根目录检测、测试文件关联

## 4. 引擎选择策略

```
输入文件 → 判断语言
  │
  ├── Python (.py)
  │   ├── 首选: pyright（语义分析，通过 LSP）
  │   └── 补充: ast-grep（捕获 pyright 可能遗漏的动态调用）
  │
  └── 其他语言
      └── ast-grep（结构搜索）
```

## 5. 语言支持与扩展

目前 code-nav 的**语义分析能力仅支持 Python**（通过 Pyright LSP 引擎）。ast-grep 引擎支持所有语言的 AST 模式搜索，但不提供语义理解（类型推断、import 链追踪等）。

如果你想为其他语言（如 Go、TypeScript）添加语义级分析支持，下面说明现有的扩展接口和需要做的工作。

### 5.1 导航层：已有清晰的扩展接口

引擎层通过两个抽象基类定义了标准接口（`engines/base.py`）：

```python
class AnalysisEngine(ABC):
    """语义分析引擎 — 每种语言实现一个。"""

    @property
    def name(self) -> str: ...              # 引擎名称，如 "gopls"
    @property
    def supported_languages(self) -> list[str]: ...  # 支持的语言，如 ["go"]

    async def find_references(self, file, line, column) -> list[Reference]: ...
    async def go_to_definition(self, file, line, column) -> list[Definition]: ...
    async def get_symbol_info(self, file, line, column) -> SymbolInfo | None: ...


class PatternSearchEngine(ABC):
    """AST 模式搜索引擎 — 通常不需要新增，ast-grep 已覆盖所有语言。"""

    async def search(self, pattern, language, path, limit) -> list[AstMatch]: ...
```

**添加新语言引擎的步骤：**

1. **创建引擎文件** — 如 `engines/gopls_engine.py`，继承 `AnalysisEngine`，封装对应的 language server（gopls、typescript-language-server 等）或分析库
2. **实现三个核心方法** — `find_references`、`go_to_definition`、`get_symbol_info`，返回 `models.py` 中定义的标准数据类型（`Reference`、`Definition`、`SymbolInfo`）
3. **注册到 EngineManager** — 在 `engines/__init__.py` 的 `EngineManager.__init__` 中实例化并加入 `self._engines` 列表

```python
# engines/__init__.py
class EngineManager:
    def __init__(self, project_path=None):
        self.sg = SgEngine()
        self.pyright = PyrightEngine(project_path=project_path)
        self.gopls = GoplsEngine(project_path=project_path)  # 新增
        self._engines = [self.pyright, self.gopls, self.sg]    # 按优先级排序
```

注册后，`primitives.py` 中的 `find_references` 和 `go_to_definition` 会自动通过 `mgr.get_engines_for(language)` 选择匹配的引擎，无需修改工具层代码。结果合并、去重也自动生效。

**参考 PyrightEngine 的实现模式：**

- 惰性启动 LSP 子进程（`_ensure_ready` → `_start`）
- JSON-RPC 通信，Future-based 请求/响应匹配（`_pending` 字典）
- 崩溃自动重启（最多 3 次）
- 内部转换方法将 LSP 协议的原生类型映射为标准 `Reference`/`Definition`

### 5.2 知识图谱层：目前仅支持 Python，需要扩展

知识图谱（`indexer/`）目前硬编码为 Python：

- `ast_parser.py` 使用 Python 标准库 `ast` 模块解析，无法处理其他语言
- `builder.py` 的 `_scan_python_files()` 只扫描 `.py` 文件
- `store.py` 的 schema 本身是语言无关的（symbols、edges、modules），可以复用

**如果需要为其他语言构建知识图谱，需要：**

1. **新建语言解析器** — 如 `indexer/go_parser.py`，实现与 `ast_parser.py` 相同的输出结构（`ParseResult`，包含 `symbols` 和 `imports` 列表）。可以基于 tree-sitter 或语言原生的 AST 工具
2. **扩展 builder 的文件扫描** — 修改 `_scan_python_files` 为通用的 `_scan_source_files`，支持按语言扫描不同扩展名
3. **适配边构建逻辑** — `_build_call_edges` 等方法目前用 Python `ast` 模块分析函数体内的调用关系，需要为新语言实现等价的分析

`store.py` 和 `wiki/` 不需要修改——它们只关心符号和关系，不关心语言。

### 5.3 当前不同语言的实际能力

| 能力                        | Python                             | 其他语言                                            |
| --------------------------- | ---------------------------------- | --------------------------------------------------- |
| find_references             | pyright（精确） + ast-grep（补充） | 仅 ast-grep（基于文本模式，可能有误报）             |
| go_to_definition            | pyright（精确） + ast-grep（补充） | 仅 ast-grep（搜索 `def/func/class` 等关键字模式） |
| ast_search                  | ast-grep                           | ast-grep                                            |
| query_symbol / query_module | 支持（知识图谱）                   | 不支持                                              |
| get_wiki / save_wiki        | 支持                               | 不支持（依赖知识图谱）                              |
| 影响分析 (change_scope 等)  | 支持                               | 部分支持（基于 ast-grep 的引用查找，精度有限）      |

## 6. 项目结构

```
code-nav-mcp/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── skills/
│   └── code_nav_wiki.md         # Wiki 生成 Skill（注册为 Agent 的 slash command）
├── docs/
│   ├── architecture.md          ← 本文档
│   └── tool-api.md              # 工具 API 详细参考
└── src/
    └── code_nav_mcp/
        ├── __init__.py
        ├── __main__.py          # 入口，MCP Server 启动
        ├── server.py            # MCP Server 定义，工具注册（FastMCP）
        ├── models.py            # 数据模型与序列化
        ├── engines/
        │   ├── __init__.py      # EngineManager — 引擎生命周期管理
        │   ├── base.py          # 引擎基类
        │   ├── pyright_engine.py # Pyright LSP 封装
        │   └── sg_engine.py     # ast-grep 封装
        ├── tools/
        │   ├── __init__.py
        │   ├── primitives.py    # 低级工具: find_references, go_to_definition, ast_search, check_diagnostics
        │   ├── workflows.py     # 高级工具: get_change_scope, pre/post_change
        │   └── wiki_tools.py    # Wiki/索引工具: build_index, query_*, get/save_wiki
        ├── indexer/
        │   ├── __init__.py
        │   ├── ast_parser.py    # Python AST 解析，符号和导入提取
        │   ├── builder.py       # 全量/增量构建调度
        │   └── store.py         # SQLite 存储（symbols, edges, module_deps）
        ├── wiki/
        │   ├── __init__.py
        │   ├── manager.py       # 三级 Wiki 读写、路径映射、过期检测
        │   ├── candidates.py    # Wiki 候选计算
        │   └── templates.py     # _index.md 索引页生成
        └── utils/
            ├── __init__.py
            ├── merge.py         # 多引擎结果合并
            ├── cache.py         # 分析结果缓存
            └── project.py       # 项目检测、测试文件发现
```
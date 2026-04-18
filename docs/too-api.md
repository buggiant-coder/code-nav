# MCP 工具 API 参考

本文档定义 code-nav MCP Server 暴露的全部 12 个工具。

---

## 低级工具 (Primitive Tools)

### 1. find_references

查找一个符号（函数、类、变量）的所有引用位置。

**输入参数:**

| 参数               | 类型   | 必填 | 默认值 | 说明                                    |
| ------------------ | ------ | ---- | ------ | --------------------------------------- |
| file               | string | 是   | —     | 文件路径（绝对或相对于项目根目录）      |
| symbol             | string | 否   | ""     | 符号名称（推荐，比 line/column 更易用） |
| line               | int    | 否   | 0      | 符号所在行号（1-based）                 |
| column             | int    | 否   | 0      | 符号所在列号（0-based）                 |
| include_definition | bool   | 否   | false  | 结果中是否包含定义位置本身              |
| include_tests      | bool   | 否   | false  | 结果中是否包含测试文件中的引用          |

> symbol 和 line 至少提供一个。

**输出示例:**

```json
{
  "symbol": "calculate_total",
  "symbol_type": "function",
  "definition": {
    "file": "src/order.py",
    "line": 23,
    "column": 4
  },
  "references": [
    {
      "file": "src/checkout.py",
      "line": 45,
      "column": 12,
      "context": "total = calculate_total(cart, discount=0.1)",
      "source": "pyright",
      "confidence": "high"
    }
  ],
  "total_count": 1,
  "filtered_test_count": 0
}
```

---

### 2. go_to_definition

跳转到符号的定义位置。

**输入参数:**

| 参数   | 类型   | 必填 | 默认值 | 说明             |
| ------ | ------ | ---- | ------ | ---------------- |
| file   | string | 是   | —     | 当前文件路径     |
| symbol | string | 否   | ""     | 符号名称（推荐） |
| line   | int    | 否   | 0      | 符号所在行号     |
| column | int    | 否   | 0      | 符号所在列号     |

> symbol 和 line 至少提供一个。

**输出示例:**

```json
{
  "symbol": "OrderProcessor",
  "symbol_type": "class",
  "definitions": [
    {
      "file": "src/processors/order.py",
      "line": 15,
      "column": 6,
      "context": "class OrderProcessor(BaseProcessor):",
      "source": "pyright",
      "confidence": "high"
    }
  ]
}
```

---

### 3. ast_search

基于 AST 模式的代码结构搜索，使用 ast-grep 语法。

**输入参数:**

| 参数     | 类型   | 必填 | 默认值 | 说明                                            |
| -------- | ------ | ---- | ------ | ----------------------------------------------- |
| pattern  | string | 是   | —     | ast-grep 搜索模式                               |
| language | string | 是   | —     | 目标语言: python, javascript, typescript, go 等 |
| path     | string | 否   | "."    | 搜索路径                                        |
| limit    | int    | 否   | 50     | 最大返回结果数                                  |

**常用模式示例:**

| 目的                   | 模式                  |
| ---------------------- | --------------------- |
| 所有函数定义           | `def $FN($$$):`     |
| 所有类定义（带基类）   | `class $CLS($$$):`  |
| 所有类定义（含无基类） | `class $NAME`       |
| 调用特定方法           | `$OBJ.process($$$)` |
| 装饰器匹配             | `@app.route($$$)`   |

---

### 4. check_diagnostics

运行 Pyright 类型检查器，返回类型错误和警告——类似 IDE 中的红色/黄色波浪线。修改代码后调用，提前发现类型问题。

**输入参数:**

| 参数  | 类型   | 必填 | 默认值  | 说明                                            |
| ----- | ------ | ---- | ------- | ----------------------------------------------- |
| path  | string | 是   | —      | 文件或目录路径                                  |
| level | string | 否   | "error" | 最低报告级别："error"、"warning"、"information" |

**输出示例:**

```json
{
  "diagnostics": [
    {
      "file": "src/order.py",
      "line": 45,
      "column": 12,
      "end_line": 45,
      "end_column": 28,
      "severity": "error",
      "message": "Argument of type \"str\" cannot be assigned to parameter \"discount\" of type \"float\"",
      "rule": "reportArgumentType"
    }
  ],
  "error_count": 1,
  "warning_count": 0,
  "files_analyzed": 3
}
```

---

## 高级工具 (Workflow Tools)

### 5. get_change_scope

快速变更风险评估。在修改代码前首先调用，决定是否需要做完整的 pre_change_analysis。

**输入参数:**

| 参数   | 类型   | 必填 | 默认值 | 说明     |
| ------ | ------ | ---- | ------ | -------- |
| file   | string | 是   | —     | 文件路径 |
| symbol | string | 否   | ""     | 符号名称 |
| line   | int    | 否   | 0      | 行号     |

**输出示例:**

```json
{
  "symbol": "calculate_total",
  "symbol_type": "function",
  "scope": "module-public",
  "caller_count": 2,
  "is_exported": true,
  "has_tests": true,
  "risk_level": "medium",
  "recommendation": "Run pre_change_analysis before modifying — 2 external callers found"
}
```

**risk_level 逻辑:**

- `low` — 0 外部调用方，或 private scope → 可直接修改
- `medium` — 1-5 外部调用方 → 建议 pre_change_analysis
- `high` — 6+ 外部调用方，或被导出为公共 API → 必须 pre_change_analysis

---

### 6. pre_change_analysis

修改前影响面分析。返回所有调用方、测试覆盖和风险评估。

**输入参数:**

| 参数        | 类型   | 必填 | 默认值  | 说明                                                                                   |
| ----------- | ------ | ---- | ------- | -------------------------------------------------------------------------------------- |
| file        | string | 是   | —      | 要修改的符号所在文件                                                                   |
| symbol      | string | 否   | ""      | 要修改的符号名称                                                                       |
| line        | int    | 否   | 0       | 行号                                                                                   |
| change_type | string | 否   | "other" | 修改类型：modify_signature, rename, remove, change_return_type, change_behavior, other |

**输出示例:**

```json
{
  "symbol": "calculate_total",
  "current_signature": "(items: List[Item], discount: float = 0.0) -> Decimal",
  "callers": [
    {
      "file": "src/checkout.py",
      "line": 45,
      "function": "process_checkout",
      "call_expression": "calculate_total(cart.items, discount=coupon.value)"
    }
  ],
  "test_coverage": {
    "test_files": ["tests/test_order.py"],
    "has_direct_tests": true
  },
  "risk_assessment": {
    "level": "medium",
    "caller_count": 1,
    "reasons": ["..."]
  },
  "suggestions": ["..."]
}
```

---

### 7. post_change_validate

修改后破坏检测。检测下游代码是否因本次修改而失效。

**输入参数:**

| 参数               | 类型   | 必填 | 默认值 | 说明                                                   |
| ------------------ | ------ | ---- | ------ | ------------------------------------------------------ |
| file               | string | 是   | —     | 刚修改的文件路径                                       |
| symbol             | string | 否   | ""     | 刚修改的符号名称                                       |
| line               | int    | 否   | 0      | 行号                                                   |
| original_signature | string | 否   | ""     | 修改前的签名（来自 pre_change_analysis），用于精确对比 |

**输出示例:**

```json
{
  "status": "breaking_changes_detected",
  "new_signature": "(items: List[Item], discount: float, tax_rate: float = 0.0) -> Decimal",
  "signature_changes": ["Parameter 'discount' changed from optional to required"],
  "issues": [
    {
      "severity": "error",
      "file": "src/report.py",
      "line": 112,
      "problem": "Missing required argument 'discount'",
      "suggested_fix": "calculate_total(filtered_items, discount=0.0)"
    }
  ],
  "suggested_actions": ["Fix src/report.py:112", "Run: pytest tests/ -v"]
}
```

**status 枚举:** `clean` | `breaking_changes_detected` | `analysis_incomplete`

---

## 知识图谱工具 (Knowledge Graph Tools)

### 8. build_index

构建/更新代码知识图谱。扫描所有 Python 文件，提取符号、参数、类型、调用/导入/继承关系。

**输入参数:**

| 参数  | 类型   | 必填 | 默认值     | 说明                                            |
| ----- | ------ | ---- | ---------- | ----------------------------------------------- |
| path  | string | 否   | 项目根目录 | 项目根目录路径                                  |
| force | bool   | 否   | false      | true=全量重建，false=增量更新（仅处理变更文件） |

**输出示例:**

```json
{
  "status": "completed",
  "mode": "incremental",
  "files_scanned": 24,
  "files_updated": 2,
  "symbols_total": 156,
  "edges_total": 89,
  "duration_seconds": 0.45,
  "wiki_candidates": {
    "new": ["src/code_nav_mcp/tools/wiki_tools.py"],
    "stale": ["src/code_nav_mcp/indexer/builder.py"],
    "packages": [{"name": "src.code_nav_mcp.tools", "has_init": true}]
  }
}
```

> `query_symbol` 和 `query_module` 内置 lazy refresh，通常无需显式调用 `build_index`。显式调用适用于：全量重建（`force=true`）或获取 `wiki_candidates` 列表。

---

### 9. query_symbol

查询符号详情 + 上下游关系。基于预构建索引，毫秒级响应。

**输入参数:**

| 参数            | 类型   | 必填 | 默认值 | 说明                                                       |
| --------------- | ------ | ---- | ------ | ---------------------------------------------------------- |
| name            | string | 是   | —     | 符号名称（如 "calculate_total", "OrderProcessor.process"） |
| file            | string | 否   | ""     | 可选文件路径，用于消歧                                     |
| include_callers | bool   | 否   | true   | 包含调用方                                                 |
| include_callees | bool   | 否   | true   | 包含被调用方                                               |
| max_depth       | int    | 否   | 1      | 遍历深度（1=直接关系，2=间接关系，最大 3）                 |

**输出:** 符号签名、参数列表、返回类型、docstring、上下游调用关系图。

---

### 10. query_module

查询模块概览 — 符号列表 + 依赖关系。

**输入参数:**

| 参数    | 类型   | 必填 | 默认值 | 说明                                        |
| ------- | ------ | ---- | ------ | ------------------------------------------- |
| file    | string | 否   | ""     | 模块文件路径（如 "util/odps_util.py"）      |
| package | string | 否   | ""     | 包名（如 "util.odps_util"），与 file 二选一 |

**输出:** 模块全部符号、导入依赖、被依赖方、Wiki 状态。

---

## Wiki 工具 (Wiki Tools)

### 11. get_wiki

读取 Wiki 内容。Wiki 是三级文档系统（项目 → 包 → 模块）。

**输入参数:**

| 参数    | 类型   | 必填 | 默认值 | 说明                                                         |
| ------- | ------ | ---- | ------ | ------------------------------------------------------------ |
| module  | string | 否   | ""     | 模块文件路径（如 "util/odps_util.py"）                       |
| package | string | 否   | ""     | 包名（如 "util"）                                            |
| level   | string | 否   | ""     | 显式指定层级："project", "package", "module"。省略时自动推断 |

**输出示例:**

```json
{
  "level": "module",
  "path": ".code-nav/wiki/util/odps_util.md",
  "exists": true,
  "content": "# odps_util\n\n...",
  "last_modified": 1713340800.0,
  "is_stale": true,
  "stale_reason": "code changed since wiki was written (code: 2026-04-17, wiki: 2026-04-15)"
}
```

> `is_stale` 是实时的（mtime 对比），不依赖 `build_index`。

---

### 12. save_wiki

保存 Wiki 内容。

**输入参数:**

| 参数    | 类型   | 必填 | 默认值 | 说明          |
| ------- | ------ | ---- | ------ | ------------- |
| content | string | 是   | —     | Markdown 内容 |
| module  | string | 否   | ""     | 模块文件路径  |
| package | string | 否   | ""     | 包名          |
| level   | string | 否   | ""     | 显式指定层级  |

**输出示例:**

```json
{
  "status": "saved",
  "path": ".code-nav/wiki/util/odps_util.md"
}
```

---

## 工具间协作示意

### 代码修改流程

```
Agent 收到修改请求
    │
    ▼
get_change_scope(file, symbol)
    │
    ├── risk: low → 直接修改
    │
    └── risk: medium/high
         │
         ▼
    pre_change_analysis(file, symbol, change_type)
         │
         ▼
    Agent 执行修改
         │
         ▼
    post_change_validate(file, symbol, original_signature)
         │
         ├── status: clean
         │        │
         │        ▼
         │   check_diagnostics(file)  ← 可选，检查类型错误
         │        │
         │        └── 完成
         └── status: breaking → 修复 → 再次 validate
```

### 代码理解流程

```
Agent 需要理解代码
    │
    ▼
get_wiki(module/package/level)
    │
    ├── exists + !is_stale → 信任 wiki，跳过源码
    ├── exists + is_stale  → wiki 仅供参考，读源码验证
    └── !exists → 读源码，可选生成 wiki
         │
         ▼
query_module(file) → 了解模块结构和依赖
query_symbol(name) → 了解关键符号的上下游
```
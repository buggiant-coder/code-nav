# Skill: Code Wiki 工作流

## 概述

基于 code-nav MCP Server 的知识图谱，为代码库生成和维护业务逻辑 Wiki。
Wiki 采用三级层次结构（项目 → 包 → 模块），帮助快速理解代码库。

## 前置条件

确保 code-nav MCP Server 已注册并可用。

## 工作流

### Step 1: 构建知识图谱

调用 `build_index` 构建/更新代码知识图谱。

- 首次使用或长时间未更新：`build_index(force=true)` 全量重建
- 日常使用：`build_index()` 增量更新（只处理变更文件）

### Step 2: 查看推荐列表

`build_index` 返回的 `wiki_candidates` 字段包含三类候选：

- `packages`：需要撰写或更新 `_package.md` 的 package（每个含 .py 文件的目录都会自动出现）
- `new`：值得首次撰写 Wiki 的模块
- `stale`：已有 Wiki 但源码已更新、需要刷新的模块

不要为 trivial 模块写 Wiki — 行数 < 50、符号 < 3 的小文件，直接读源码更高效。

### Step 3: 生成项目级 Wiki

首次接触一个项目时，生成项目级概览：

1. 查看 `_index.md` 了解项目结构
2. 对核心入口模块调用 `query_module`，理解整体架构
3. 阅读关键模块源码
4. 调用 `save_wiki(level="project", content="...")` 保存

### Step 4: 生成包级 Wiki

遍历 `wiki_candidates.packages` 中的所有候选，为每个 package 生成 `_package.md`：

1. 对包内各模块调用 `query_module`，理解分工
2. **如果候选中 `has_init` 为 `true`**，必须阅读该 package 的 `__init__.py` 源码，将其导出的公共 API 作为 `_package.md` 的核心内容（`__init__.py` 不会单独生成模块级 Wiki，其内容完全由 `_package.md` 承载）
3. 阅读关键模块源码，理解该包的职责定位
4. 调用 `save_wiki(package="<package_name>", content="...")` 保存

**注意**：每个含 `.py` 文件的目录都会自动被发现为 package 候选，确保全部处理。

### Step 5: 生成模块级 Wiki

按 `wiki_candidates.new` 的优先级，逐个分析模块：

1. 调用 `query_module(file="<module_path>")` 获取模块结构化信息 — 返回值中的 `suggested_wiki_lines` 指导正文篇幅，`dependencies` / `dependents` 用于填充末尾的强制引用关系章节
2. 调用 `query_symbol(name="<symbol_name>")` 了解关键符号的上下游关系
3. 阅读关键函数的源码
4. 按模块级 Wiki 结构撰写（见下方"模块级 Wiki 必须结构"）
5. 调用 `save_wiki(module="<module_path>", content="...")` 保存

### Step 6: 查阅已有 Wiki

后续工作中，修改代码前先查阅相关模块的 Wiki：

- `get_wiki(module="<module_path>")` — 查看模块级 Wiki
- `get_wiki(package="<package_name>")` — 查看包级 Wiki
- `get_wiki(level="project")` — 查看项目级 Wiki

## Wiki 三级层次

| 层级   | 文件                  | 内容定位                               | 篇幅指导                                              |
| ------ | --------------------- | -------------------------------------- | ----------------------------------------------------- |
| 项目级 | `_project.md`       | 项目做什么、核心业务流程、模块协作关系 | 500-1000 字                                           |
| 包级   | `{pkg}/_package.md` | 该包的定位、模块分工、协作关系         | 200-500 字                                            |
| 模块级 | `{module}.md`       | 具体业务逻辑、关键规则、注意事项       | 参考 `query_module` 返回的 `suggested_wiki_lines` |

## 图谱查询工具参考

| 场景                         | 工具                | 说明                       |
| ---------------------------- | ------------------- | -------------------------- |
| 了解一个函数的作用和上下游   | `query_symbol`    | 基于预构建索引，毫秒级响应 |
| 了解一个模块的整体职责和依赖 | `query_module`    | 返回模块全部符号和依赖关系 |
| 需要实时精确的引用信息       | `find_references` | 实时分析，保证最新但更慢   |

## 模块级 Wiki 必须结构

每个模块级 Wiki 必须包含以下两部分：

### 1. 正文

自由撰写，篇幅参考 `query_module` 返回的 `suggested_wiki_lines`。内容聚焦业务逻辑和设计意图。

### 2. 强制章节：依赖与被调用关系

每个模块级 Wiki 末尾**必须**包含以下固定格式的章节，数据从 `query_module` 返回的 `dependencies` 和 `dependents` 字段填充（仅项目内部模块间依赖）。此章节不算入正文篇幅。

```markdown
## 依赖与被调用关系

### 本模块依赖
- `module_a.py` — 使用: FuncX, ClassY
- `module_b.py` — 使用: FuncZ

### 被以下模块依赖
- `module_c.py` — 使用了本模块的: FuncA, ClassB
- `module_d.py` — 使用了本模块的: FuncC
```

- 若某方向为空（无依赖或无被依赖），写"无"
- `dependencies` 中每项的 `imports` 字段即为"使用"的具体符号名
- `dependents` 中每项的 `imports` 字段即为对方使用的本模块符号名

## 撰写要点

- **使用中文撰写** — Wiki 内容必须使用中文，包括标题、正文、注释。代码标识符（函数名、类名等）保持原样即可
- **写业务逻辑，不写代码结构** — "计算含税价格并取整"比"调用 round() 和 multiply()"有用
- **写隐含知识** — 代码能看到的不用重复，要写的是"为什么这么做"
- **写上下文** — 这个模块在整个业务流程中处于什么位置
- **保持简洁** — 正文篇幅参考 `suggested_wiki_lines`，不要事无巨细
- **利用图谱数据** — `query_symbol` 和 `query_module` 返回的依赖关系是撰写 Wiki 的重要输入
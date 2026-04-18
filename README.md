# code-nav

让Agent用上IDE。

## 安装

```bash
# 前置：安装 ast-grep CLI
brew install ast-grep    # macOS
# 其他系统参考 https://ast-grep.github.io/guide/quick-start.html

# 安装 code-nav（Python >= 3.10，自动安装 pyright）
cd code-nav
pip install -e .
```

> **依赖说明**：`pip install` 会自动安装 `pyright`（Pyright LSP 引擎，用于 Python 语义分析）。`pyright` pip 包内嵌了 Node.js 运行时，无需额外安装 Node.js。

## 配置

安装后需要完成三步配置：注册 MCP、注册 Skill、配置 Agent 规范。

### 1. 注册 MCP Server

**Claude Code CLI：**

```bash
claude mcp add --scope user code-nav -- /path/to/python -m code_nav
```

> `/path/to/python` 替换为安装了 code-nav 的 Python 解释器路径。用 `--scope user` 注册为全局，所有项目都可用。

**其他 Agent（手动配置）：**

```json
{
  "mcpServers": {
    "code-nav": {
      "command": "/path/to/python",
      "args": ["-m", "code_nav"]
    }
  }
}
```

### 2. 注册 Skill

将 `skills/code_nav_wiki.md` 注册为 Agent 的 slash command，用于引导 Agent 按标准流程生成代码库 Wiki。

**Claude Code：**

```bash
# 全局注册
cp skills/code_nav_wiki.md ~/.claude/commands/code_nav_wiki.md
```

注册后可通过 `/code_nav_wiki` 触发 Wiki 生成工作流。

> 知识图谱（`graph.db`）和 Wiki 文档均保存在目标项目的 `.code-nav/` 目录下。建议将 `.code-nav/` 纳入版本管理——Wiki 文档记录了代码的业务逻辑和设计意图，与代码一起演进才能发挥最大价值。`graph.db` 可按需选择是否提交（体积较小，提交后新成员无需首次 `build_index`；不提交则由 lazy refresh 自动生成）。

### 3. 配置 Agent 规范

仅注册 MCP 和 Skill 不够——Agent 不会主动使用 code-nav 的语义工具和影响分析流程，也不会在 commit 后检查 Wiki 状态。需要将开发规范注入 Agent 的提示词上下文。

下面的规范包含两部分：**代码导航与影响分析**（让 Agent 优先使用语义工具、修改代码前走影响分析流程）和 **Wiki 维护**（让 Agent 正确处理 stale Wiki、commit 后提醒更新）。

<details>
<summary><b>规范内容（点击展开）</b></summary>

```markdown
## code-nav 工具使用规范

当 code-nav MCP Server 可用时，必须遵守以下规范。

### 代码导航：优先使用语义工具

code-nav 的语义工具比文本搜索更准确，**必须优先使用**：

- **查找引用** → `find_references`（不要用 Grep 搜函数名）
- **跳转定义** → `go_to_definition`（不要用 Grep 搜 `def xxx`）
- **AST 模式搜索** → `ast_search`（如 `def $FN($$$):` 搜所有函数定义）
- **符号查询** → `query_symbol`（查上下游关系，毫秒级）
- **模块查询** → `query_module`（查模块全部符号和依赖）

只有在搜索非代码内容（配置文件、文档、字符串常量）时才用 Grep。

### 修改代码：必须走影响分析流程

**每次修改函数、类、方法签名前**，必须按以下流程操作：

1. **`get_change_scope`** — 快速风险评估
   - LOW → 可直接修改
   - MEDIUM/HIGH → 必须进入第 2 步
2. **`pre_change_analysis`** — 深度分析所有调用方、测试覆盖、风险评估
3. **执行代码修改**
4. **`post_change_validate`** — 验证所有调用方兼容新签名

跳过此流程可能导致破坏性变更未被发现。

### Wiki 使用规范

#### 读取 Wiki
修改代码前，先调用 get_wiki 了解模块上下文：
- is_stale: false → 信任 wiki 内容，可跳过源码阅读
- is_stale: true  → wiki 仅供参考，必须读源码验证关键细节

#### Commit 后检查
每次 git commit 后，调用 build_index 检查 wiki_candidates：
- 若 stale 或 new 列表不为空，告知用户有 Wiki 需要更新
- 用户确认后，按 /code_nav_wiki 工作流更新对应模块的 Wiki
```

</details>

#### 注册方式

**方式 A：全局注册（推荐）**

将规范写入 `~/.claude/CLAUDE.md`，所有项目的所有对话都会自动加载，无需逐项目配置：

```bash
# 将上述规范内容追加到全局 CLAUDE.md
cat >> ~/.claude/CLAUDE.md << 'EOF'
## code-nav 工具使用规范
...（粘贴上方规范内容）
EOF
```

适合：MCP Server 已用 `--scope user` 全局注册的场景。

**方式 B：按项目注册**

将规范写入项目根目录的 `CLAUDE.md`（或 `.cursor/rules` 等），仅对该项目生效：

```bash
# 在项目的 CLAUDE.md 中加入规范
cat >> /path/to/project/CLAUDE.md << 'EOF'
## code-nav 工具使用规范
...（粘贴上方规范内容）
EOF
```

适合：仅部分项目需要 code-nav 的场景，或团队共享项目规范（CLAUDE.md 可提交到 Git）。

> **提示**：两种方式可以组合使用。全局 CLAUDE.md 放通用规范，项目 CLAUDE.md 放项目特有的信息（如构建命令、Wiki 规则等）。

#### 可选：配置 Hook 强化规范执行

提示词规范依赖 Agent 自觉遵守，实际使用中 Agent 可能忘记。通过配置 hook，可以在特定时机**自动注入提醒**，弥补 Agent 的"健忘"。

##### Hook A：Grep 拦截与提醒

Agent 习惯性调用 Grep 搜函数名、类名，而非 code-nav 的语义工具。此 hook 通过 **PreToolUse** 实现两级策略：

- **Block**：当 Grep 的 pattern 匹配代码定义搜索（`def `、`class `、`async def `、`import `、`from ... import `）时，直接阻断并提示使用语义工具
- **Warn**：其他 Grep 调用正常放行，但注入 systemMessage 提醒 Agent 优先使用语义工具

**Claude Code** — 在 `~/.claude/settings.json`（全局）或 `.claude/settings.json`（项目级）中配置：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Grep",
        "hooks": [
          {
            "type": "command",
            "command": "input=$(cat); if echo \"$input\" | grep -qiE '\"pattern\".*\"(def |class |async def |import |from .+ import )'; then echo '{\"decision\": \"block\", \"reason\": \"[code-nav] 禁止用 Grep 搜索代码定义(def/class/import)。请改用: go_to_definition(跳转定义), find_references(查找引用), ast_search(AST搜索, 如 def $FN($$$):)\"}'; else echo '{\"systemMessage\": \"[code-nav 警告] 你正在使用 Grep。如果你在搜索代码符号（函数定义、类、引用），这是错误的做法！必须改用 go_to_definition / find_references / ast_search / query_symbol。Grep 仅允许用于非代码内容（配置文件、文档、字符串常量）。\"}'; fi"
          }
        ]
      }
    ]
  }
}
```

> **注意**：hook 的工具输入通过 stdin 以 JSON 传入，需用 `input=$(cat)` 读取。

##### Hook B：Commit 时提醒检查 Wiki

commit 表示用户对改动已满意，是检查 wiki 差异的自然时机。此 hook 通过 **PreToolUse** 检测 Bash 工具输入中是否包含 `git commit`，若匹配则注入强制提醒，要求 Agent 立即调用 `build_index` 检查 stale wiki。

**Claude Code** — 在 `~/.claude/settings.json`（全局）或 `.claude/settings.json`（项目级）中配置：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "input=$(cat); echo \"$input\" | grep -q 'git commit' && echo '{\"systemMessage\": \"[Wiki 必须执行] 检测到 git commit。你必须立即调用 build_index 检查 wiki_candidates，如果 stale 或 new 列表不为空，告知用户哪些 Wiki 需要更新。不要跳过这一步。\"}' || true"
          }
        ]
      }
    ]
  }
}
```

> 两个 hook 可合并到同一个 `PreToolUse` 数组中。完整配置示例见下方。

<details>
<summary><b>合并后的完整 hooks 配置</b></summary>

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Grep",
        "hooks": [
          {
            "type": "command",
            "command": "input=$(cat); if echo \"$input\" | grep -qiE '\"pattern\".*\"(def |class |async def |import |from .+ import )'; then echo '{\"decision\": \"block\", \"reason\": \"[code-nav] 禁止用 Grep 搜索代码定义(def/class/import)。请改用: go_to_definition(跳转定义), find_references(查找引用), ast_search(AST搜索, 如 def $FN($$$):)\"}'; else echo '{\"systemMessage\": \"[code-nav 警告] 你正在使用 Grep。如果你在搜索代码符号（函数定义、类、引用），这是错误的做法！必须改用 go_to_definition / find_references / ast_search / query_symbol。Grep 仅允许用于非代码内容（配置文件、文档、字符串常量）。\"}'; fi"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "input=$(cat); echo \"$input\" | grep -q 'git commit' && echo '{\"systemMessage\": \"[Wiki 必须执行] 检测到 git commit。你必须立即调用 build_index 检查 wiki_candidates，如果 stale 或 new 列表不为空，告知用户哪些 Wiki 需要更新。不要跳过这一步。\"}' || true"
          }
        ]
      }
    ]
  }
}
```

</details>

**Cursor** — 在 `.cursor/rules` 中配置：

```
## Wiki Maintenance

This project uses code-nav MCP Server for code wiki.
After any git commit, call the MCP tool `build_index` to check
wiki_candidates. If stale or new lists are non-empty, inform the
user which wikis need updating.
```

## 提供的工具

注册后 Agent 将获得 12 个工具：

| 分类       | 工具                     | 用途                                 |
| ---------- | ------------------------ | ------------------------------------ |
| 代码导航   | `find_references`      | 查找符号的所有引用（比 grep 更准确） |
|            | `go_to_definition`     | 跳转到符号定义位置                   |
|            | `ast_search`           | AST 结构模式匹配搜索                 |
| 诊断       | `check_diagnostics`    | 运行类型检查，返回错误和警告         |
| 影响面分析 | `get_change_scope`     | 快速评估修改风险等级                 |
|            | `pre_change_analysis`  | 改前深度影响面分析                   |
|            | `post_change_validate` | 改后破坏性变更检测                   |
| 知识图谱   | `build_index`          | 构建/更新代码知识图谱                |
|            | `query_symbol`         | 查询符号详情及上下游关系             |
|            | `query_module`         | 查询模块概览及依赖关系               |
| Wiki       | `get_wiki`             | 读取业务逻辑文档                     |
|            | `save_wiki`            | 保存业务逻辑文档                     |

## Wiki 自动维护

code-nav 提供三级 Wiki 系统（项目 → 包 → 模块），帮助 Agent 快速理解代码库的业务逻辑。以下是内置的自动维护机制：

### 知识图谱自动刷新（Lazy Refresh）

`query_symbol` / `query_module` 依赖 `graph.db` 中的引用关系。当源码变更后，这些关系可能过期。

**code-nav 已内置 lazy refresh 机制**：每次调用 `query_symbol` / `query_module` 时，自动检测文件 mtime 变化，若有变更则先执行增量 `build_index`（~0.5s），再返回查询结果。Agent 无需显式调用 `build_index`。

### Wiki 过期检测

`get_wiki` 返回的 `is_stale` 字段是**实时**的：直接比较源码文件与 wiki 文件的 mtime，不依赖 `build_index`。Agent 每次读取 wiki 都能获得准确的过期状态。

### Wiki 更新时机

Wiki 生成需要 LLM 参与，成本较高，不适合自动执行。推荐在 `git commit` 后提醒用户检查——commit 表示用户对改动已满意，是检查 wiki 差异的自然时机。

配置方法见上方「[3. 配置 Agent 规范](#3-配置-agent-规范)」章节。

## 开发

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v   # 126 tests
```

## 语言支持

目前 code-nav 的语义分析能力（知识图谱、影响面分析、Wiki 系统）仅支持 **Python**。ast-grep 引擎支持所有语言的 AST 模式搜索，但不提供语义理解。

如需为其他语言（如 Go、TypeScript）添加语义级支持，可自行扩展。详见[架构设计 — 语言支持与扩展](docs/architecture.md#5-语言支持与扩展)。

## 文档

- [架构设计](docs/architecture.md)
- [工具 API 参考](docs/tool-api.md)

---
name: codewise
description: 项目级记忆库生成与维护 — 扫描代码库、全部 Git Worktree 与项目相关的多 Agent 会话（Codex、Claude Code、Gemini CLI、OpenCode、Cursor、Aider 及可扩展来源），生成网状互链的模块化知识库（独立 git 仓库，挂在 git-common-dir 下），帮助人和 AI 快速理解项目。支持首次生成、增量更新、强制重建和局部刷新，并包含多代码树检测、接口契约速查、pitfalls/decisions 交叉验证与架构整体观。Use when user says "codewise", "/codewise", "$codewise", "knowledge", "生成知识库", "更新知识库", "重建知识库", "document the project", "create knowledge base", or when onboarding to a new project and need structured documentation.
---

# 项目知识库

扫描代码库，生成网状互链的模块化知识库。AI 读 INDEX.md 按需跳转，不挤上下文。

**核心原则：主代理编排，子代理分片精读。** 使用当前运行时可用的子代理并行读取代码和会话；主代理负责 scope、事实边界、交叉验证与最终写入。若当前运行时不支持子代理，按相同分组串行处理，不得跳过任何文件或会话。

## 目标

从用户本次请求或 Skill 调用参数中读取模式、路径与 scope description；不要依赖某个客户端专属的 `$ARGUMENTS` 注入。

---

## Phase 0：解析 scope

扫描用户本次请求，提取第一个**实际存在的目录路径**作为 `<ROOT>`（子项目根）；其余文字作为 scope description 保留给后续阶段。若未提供路径，`<ROOT>` 默认为 `.`（整个仓库，行为与旧版一致）。

**知识库位置：** 知识库不在主仓库工作树里，而是 `git-common-dir` 下的独立 git 仓库。开始前完整读取 [references/storage-layout.md](references/storage-layout.md)，据此解析 `<KB>`、`<SCOPE>` 与本次读写的 `<KBR>`，并完成 `_identity.json` 校验与加锁。**必须用 `--path-format=absolute`**，否则主工作树与 Worktree 会读写两个不同位置。

**之后所有路径占位符按以下规则解析**：
- 知识库输出位置：`<KBR>/`（`<KBR>` 由归属分支决定，见 [references/branch-resolution.md](references/branch-resolution.md)）
- 注册指引写入：`<ROOT>/AGENTS.md` 与 `<ROOT>/CLAUDE.md`（按 Phase 6 的兼容策略）
- 代码扫描范围：Phase 1 读文档、Phase 1.2 子代理扫描范围、Phase U 的 `git diff` pathspec，全部限定在 `<ROOT>` 内
- 子项目外的文件不读、不扫、不链接。仓库根的 `AGENTS.md` / `CLAUDE.md` 可作为外层上下文参考，但不作为事实源，也不修改

**会话来源与 Worktree 项目身份：** 不要从当前 cwd 手拼 Claude Code 目录。完整读取 [references/session-sources.md](references/session-sources.md)，并始终用 `scripts/discover_sessions.py` 生成统一会话清单。项目身份以 Git `git-common-dir` 为锚点；同一项目的主工作树、Codex 临时工作树与其他 `git worktree` 都要覆盖。目录/hash 命中只是候选，子项目必须再用文件操作、tool cwd、diff 等内容证据过滤。

**已删除 Worktree 的会话打捞（必做）：** 若 INDEX 元信息区有 `known_worktrees`，把每一项通过 `--known-worktree` 回传给发现脚本。这些路径已不在 `git worktree list` 中，但会话文件仍在磁盘上——不回传就永久发现不了。每次运行结束都要把脚本输出的 `project.known_worktrees` 原样写回元信息区（见 Phase 4 / U.6）。标记 `stale=true` 的 Worktree 只用于会话路径匹配，没有 HEAD/branch，因此其会话一律按 `unknown-branch` 保守处理。

**报告 scope 与归属：** 开始 Phase 1 前向用户告知解析结果——`<ROOT>`、`<KBR>` 的位置、本次归属的分支目录（例如"将为 `apps/web/` 生成知识库，写入 `<KB>/apps-web/main/`，归属主线"）。**报告后直接继续，不阻塞。**

**唯一需要停下来问的情况**：首次在某个非主线分支上运行，且该分支还没有自己的知识库目录——此时按 [references/branch-resolution.md](references/branch-resolution.md) 展示推导出的归属，问一次是否建独立目录。这个选择记入分支登记表，同一分支只问一次。

**过期检测（仅当 `<KBR>/INDEX.md` 已存在 + 主仓库是 git 仓库时执行）：** 读元信息区，提取 `baseline_commit`，报告距上次同步的累计 commit 数：

```bash
comparison_base=$(git merge-base <baseline_commit> HEAD || true)
if [ -n "$comparison_base" ]; then
  git rev-list --count "$comparison_base"..HEAD -- <ROOT>
else
  echo "baseline 与当前 HEAD 没有共同祖先；无法计算可靠的 commit 数。"
fi
```

把累计 commit 数和 `synced_at` 一并告知用户（例如"距上次同步 2026-04-15 累计 23 个 commits"）；若 `comparison_base` 与 `baseline_commit` 不同，说明发生过 rebase/分叉，用共同祖先计数并告知。**这只是信息披露，不阻塞流程。** 若 `baseline_commit` 不存在、commit 已被 rebase 冲掉、或非 git 仓库，提示原因并继续——后续 Phase U 会按兜底策略走（询问用户或 mtime）。

分支归属由目录结构物理保证（每个分支读写自己的目录），因此**不需要分支漂移检测**：不会出现"知识库在另一条分支上生成"的情况。仍需坚持的是事实边界——只有当前代码、当前 HEAD 的历史或明确的会话历史分支证据能验证时，才把会话结论写入当前状态条目。

---

## 模式判断

**先做旧格式守卫。** `<ROOT>/docs/knowledge/INDEX.md` 存在、而 **`<KB>/<SCOPE>/main/INDEX.md`** 不存在时：**停止并报告需要人工迁移，不做任何写入。** 这是旧版把知识库放在工作树里留下的产物，把它当成"首次生成"会重新全库扫描并丢弃已有的手写条目。迁移需要人工判断，不在本 skill 范围内（详见 [references/storage-layout.md](references/storage-layout.md)）。

守卫判定的是 `main/`，**不是 `<KBR>`**——它问的是"这个 scope 迁移过没有"，与当前归属哪条分支无关。用 `<KBR>` 会在"主线已迁移、但在功能分支上首次建独立目录"时误触发（此时 `branches/<slug>/INDEX.md` 本就不存在），把正常流程整个卡死。

然后检查 `<KBR>/INDEX.md` 是否存在：

- **不存在** → 首次生成（Phase 1-6，包含代码全文件精读 + 项目相关会话全扫）
- **存在 + 无参数** → **默认增量更新（Phase U）**
- **存在 + 参数含 `update`** → 增量更新（Phase U，等价于无参数）
- **存在 + 参数含 `rebuild`** → 强制重建（重新执行首次生成流程，会覆盖现有条目；**必须向用户明确告知并等待确认后再执行**）
- **存在 + 参数含 `refresh-docs`** → **局部刷新 INDEX 的"项目文档导航"区**(Phase R,只跑 Step 4.6 + Phase 1.1 文档简介,不动其他条目,不更新 baseline_commit)
- **存在 + 参数含 `refresh-interfaces`** → **局部刷新 INDEX 的"接口契约速查"区**(Phase R,只跑 Step 4.5,同上不动其他)
- **存在 + 参数含 `merge <branch>`** → **并入分支知识库**（Phase M，把 `branches/<slug>/` 三方合并进 `main/`）

**重建路径必须显式触发，默认走 update。** 这是为了保护用户手动编辑过的条目，避免误覆盖。

**`refresh-*` 路径用于局部场景**(只改了文档/只加了 Tauri command,想立刻刷 INDEX 不想跑完整 update)。详见 Phase R。

**没有"快速版"模式。** 我们的定位是"项目级记忆库",首次建立 baseline 就要完整——代码 + 会话历史一次消化干净。如果项目极大导致首次成本过高,接受这个成本,或缩小 `<ROOT>` 范围分子项目跑。

---

## Phase 1：探测

先用 **Phase 1.0 机械检测代码树和扩展名清单**,再读已有文档,最后子代理基于事实清单做归类分析。

### 1.0 多代码树检测 + 动态扩展名推断

完整规则（代码树检测、monorepo 判定、扩展名推断、排除规则、接口契约抽取、项目文档清单抽取）见 [references/detection.md](references/detection.md)。

**主流程必须真做这步。** 所有检测到的代码树都要覆盖，**不能假设单一 `src` 入口**；检测结果要先报告给用户再进入 Phase 1.1。

### 1.1 读取已有文档（直接读，不用子代理，全部限定在 `<ROOT>` 内）：
- `<ROOT>/CLAUDE.md`、`<ROOT>/README.md`、`<ROOT>/AGENTS.md` — 项目说明
- `<ROOT>/package.json`、`<ROOT>/Cargo.toml`、`<ROOT>/go.mod`、`<ROOT>/Podfile`、`<ROOT>/project.yml` 等 — 技术栈和依赖
- `<ROOT>` 下的设计文档、架构文档 — 常见位置如 `docs/`、`doc/`、`design/`、`specs/`
- 仓库根（`<ROOT>` 之上）的 `AGENTS.md` / `CLAUDE.md` 可快速浏览作为外层上下文，但不作为事实源

**已有文档的使用策略：读文档获取意图，读代码验证事实，冲突时以代码为准。**
- 文档中的架构描述、模块划分 → 作为 Phase 2 规划条目的参考输入
- 文档中的设计决策、选型原因 → 直接吸收到 `decisions/` 条目（这类信息代码里看不出来，文档是唯一来源）
- 文档中的技术细节（用了什么、怎么配置）→ 必须用代码验证，项目文档经常过时
- 如果文档和代码矛盾 → 条目以代码为准，可以在条目中标注"文档称 X，实际代码为 Y"

**写"项目文档导航"简介(必做)** ⭐:

对 Phase 1.0 Step 4.6 抽取的**每个文档完整读一遍**(使用当前运行时的文件读取工具读全文,不要只看 H1+第一段),然后写一段 50-100 字的简介:

- 第一句:**这个文档讲了什么**(主旨)
- 第二句:**关键内容 / 适用场景**(让 AI 判断要不要打开)
- 第三句(选填):**跟其他文档的关系**(比如 progress 文档是 plan 文档的进度跟进)

✅ 完整读后:"服务器管理操作指南。包含 SSH 连接(`ssh snap-server`)、项目目录(`/app`)、变更日志记录方式。**操作服务器前必读**——CLAUDE.md 红线明示。"

❌ 浅扫产物:"服务器配置文档"(只看 H1 写出来的废话)

**为什么必须完整读**:简介的价值是让 AI **快速判断要不要打开这个文档**。只看 H1+第一段写出来的简介经常是文档自己重复(`# 服务器配置 → 这是关于服务器配置的文档`),没有信息增量。完整读后的简介才能告诉 AI 文档的真实内容轮廓。

**这些简介会写进 INDEX.md 的"## 项目文档导航"区**(详见 Phase 4)。

**主动识别架构图** ⭐:

读文档时**优先识别已有的架构描述**:
- ASCII 架构图(README/CLAUDE.md/docs 里的 `┌─┐/└─┘` 块)
- mermaid/dot 图(README 里的 ` ```mermaid ` 块)
- 数据流描述("用户操作 → 前端 X → 后端 Y → 云端 Z" 这种)
- 模块依赖图

**这些已有的架构资产应该原样吸收(或基于它扩展)到 `domains/architecture-overview` 条目**,不要 LLM 凭印象重写。已有的架构图作者本人最了解系统,LLM 重写容易丢细节或写错。

如果已有文档**没有**架构图(纯 README 介绍功能),Phase 2 规划 architecture-overview 时再让子代理基于代码事实生成。

**1.2 启动子代理（并行）扫描源码，范围严格限定在 Phase 1.0 检测出的代码树清单内：**

每个子代理的任务模板:

```
扫描分配给本子代理的代码树/目录的所有源码文件,分析并报告:
1. 每个文件的核心类型(class/struct/enum/function/module)和一句话职责
2. 文件之间的依赖关系(谁引用了谁)
3. 被 3 个以上文件引用的公共类型/函数
4. 按功能域归类(如:用户认证、数据处理、API 层、UI 组件等)
5. 第三方依赖及其用途
6. 非显而易见的设计模式或架构决策
7. UI/设计规范:扫描主题配置文件(uno.config、tailwind.config、theme.ts、CSS 变量文件等),识别主题色、暗色模式、视觉效果、间距/圆角约束。归入 shared/ 分类

**严格只扫描分配给本子代理的代码树,`<ROOT>` 之外或未分配的代码树一概不读、不引用。**

**扩展名按 Phase 1.0 推断的清单**(主流程已注入到本子代理的输入参数),不要去扫清单外的扩展名。

**跳过第三方/vendor 代码,只分析项目自身的源码。** 包括但不限于:node_modules、vendor、Pods、uni_modules、.nuxt、.next、dist、build、target、generated 等目录,以及任何看起来是外部库或自动生成的代码。

代码树/目录:[本子代理分配的代码树或目录列表]
扩展名:[Phase 1.0 推断的扩展名清单]
输出格式:按功能域分组,每个文件一行。最后单独列出公共模块和跨域依赖。
```

**子代理分配策略**(基于 Phase 1.0 检测的代码树数量):
- 单代码树小项目(<50 文件):1 个子代理
- 单代码树中项目(50-200 文件):2 个子代理,按目录分
- 单代码树大项目(>200 文件):3 个子代理,按模块/层级分
- **多代码树项目**(Phase 1.0 检测到 ≥2 棵):**每棵代码树至少分配一个子代理**,确保都被覆盖;大代码树再按目录拆

## Phase 2：规划

根据探测结果，规划知识库条目。

### 2.1 确定条目列表

按 6 个分类规划：

| 分类 | 目录 | 内容 |
|------|------|------|
| 功能域 | `domains/` | 按业务功能划分的模块 |
| 公共模块 | `shared/` | 被多处引用的类型/工具/组件 |
| 设计决策 | `decisions/` | 关键技术选型和架构决策 |
| 外部集成 | `integrations/` | 第三方依赖的集成方式 |
| 工作流 | `workflows/` | 跨模块的操作流程 |
| 踩坑记录 | `pitfalls/` | 已知陷阱和注意事项 |

**架构整体观条目(必须有)** ⭐:

domains 中**必须有一个 `architecture-overview` 或 `app-shell` 类的全景条目**,统摄项目所有代码树(前端+后端+云端等),内容包括:
- 整体架构图(文本/ASCII,展示前端、桌面端、云端等子系统的关系)
- 数据流全景(用户操作如何穿越各子系统)
- 各代码树的职责一句话总结
- 关键 IPC/通信桥梁(Tauri commands、HTTP API、云函数调用等)

这个条目放在 INDEX.md 的"按功能域"区域**第一项**,新人接手项目时第一个读。

**为什么必须有**:实测发现,如果只列各个独立 domain(streaming/sidebar/recognition/...),新人读完 60 个 domain 也得不到"这个项目长什么样"的整体感。架构整体观是知识库的"目录索引页",不是冗余内容。

**拆分原则:一个条目只讲一件事。** 判断标准:
- 条目里出现了两个独立的数据流 → 拆
- 条目里出现了两组不相关的文件 → 拆
- 条目里有两个可以独立理解的子功能 → 拆
- 拆完后单个条目低于 20 行 → 不拆,合并到相关条目

**粒度参考:** 每个条目 30-80 行。超过 100 行大概率该拆,低于 20 行大概率该合并。宁可多几个小条目,也不要一个大杂烩。

**反"过度删减"约束** ⭐:

实测发现 LLM 在简化的名义下容易**过度删减**有用条目。明确禁止:
- ❌ 不要为了"简化"删除 integrations 条目——任何被代码 import 且非内部库的第三方依赖都应有对应条目
- ❌ 不要把 5 个独立的 shared 组件合并成 1 个 `shared-components.md`(违反"一个条目只讲一件事")
- ❌ 不要把多个独立的 pitfall 合并成 `xx-pitfalls.md` 这种打包条目(详见 Phase 3.6.5)
- ❌ 不要为了"简洁"砍掉架构整体观条目

**判定**:如果某次产出的某个分类**条目数显著少于原版**(前一次该项目有 N 条,本次只有 M < N 条),且差异不能用"原版有重复"解释——很可能是 LLM 过度合并/删减,**应该回头检查**。

### 2.2 展示规划

列出每个条目的标题和一句话描述，然后**直接继续生成**——不阻塞等待。条目是独立文件，生成后要调整比事前逐条确认便宜得多，而中途停顿会让 codewise 无法无人值守运行。

**只有两种情况停下来问**：

- 条目数异常（> 80 或 < 5）——通常意味着 scope 划错了
- 检测到 monorepo 且 `<ROOT>` 是仓库根（Phase 1.0 已有此规则）

**不需要的分类可以跳过。** 比如纯库项目可能没有 workflows，新项目可能没有 pitfalls。空分类不创建目录。

## Phase 3：生成

### 3.1 创建目录结构

```bash
mkdir -p <KBR>/{domains,shared,decisions,integrations,workflows,pitfalls}
```

只创建有条目的分类目录。

**同时写 `<KBR>/_meta.json`**，记录本目录的归属：

```json
{ "anchor_kind": "main", "branch": "<git branch --show-current 的实际结果>" }
```

分支目录还要额外记 `forked_from`（当时知识库仓库的 HEAD），那是三方合并取 base 的唯一依据。**主线分支名必须实测写入，不能假定是 `main`** —— 目录名是 `main`，分支名可能是 `master`。

### 3.2 并行生成条目（代码全文件精读）

**两阶段策略：Phase 1 快扫定结构，Phase 3 精读写内容。** 没有"快速版"——首次生成默认就要高质量。

为每个条目（或相关的一组条目）启动子代理，子代理必须：

1. **全文件精读** — 读取项目中每一个源码文件的完整内容（在子代理负责的范围内），不只是条目相关的文件
2. **交叉验证条目规划** — 读完代码后，回头检查 Phase 2 的条目规划是否遗漏了功能点，遗漏的追加
3. **深挖隐藏逻辑** — 重点扫描：
   - 事件监听、定时任务、生命周期钩子中的副作用
   - 动态 import、条件加载、运行时注册的模块
   - 错误处理中的 retry、fallback、降级策略
   - 注释中的 HACK/FIXME/TODO/WORKAROUND
4. **基于实际代码写条目**，不要靠 Phase 1 的概览信息猜测

**分组策略：** 按目录结构分组（顶层目录或功能域），每个子代理负责一组，控制在 100 个文件以内。这样确保所有源码文件被精读到。

**每个条目是独立的 md 文件，遵循对应分类的模板。**

<!-- TEMPLATES_PLACEHOLDER -->

### 3.3 全项目会话扫描（必做）

代码告诉你“当前是什么”，会话告诉你“为什么、怎么演变、哪些尝试失败过”。**pitfalls、decisions 和改动意图的重要素材常只存在于历史会话中。** 这一步与 3.2 并行运行。

**操作步骤：**

1. 完整读取 [references/session-sources.md](references/session-sources.md)。
2. 运行统一发现脚本（`<SKILL_DIR>` 是本 Skill 目录）：

   ```bash
   python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> --pretty
   ```

3. 向用户报告检测到的 Worktree、来源、候选会话数，以及“检测到但无法自动解析”的来源。候选为 0 的来源不报错。
4. 每个候选会话启动一个子代理；数量很大时可分批并行，但**不得抽样、只读标题或只读最终回复**。数据库型来源按 `path + selector` 区分会话。
5. 子代理先做 scope 内容过滤，再按参考文件的“会话提取协议”输出归一化摘要。主流程按 `provider + session_id` 去重，并把 Worktree 路径映射成 `<ROOT>` 相对路径。
6. 将会话摘要与 3.2 当前代码精读结果、当前分支 git 历史和对应 Worktree 状态交叉验证：
   - 同一根因被代码注释 + 多个会话印证 → 高置信度，必入条目。
   - 其他 Worktree 的实现已出现在当前 `<ROOT>` → 可作为当前事实。
   - 仅存在于未合并分支、已回滚或已删除代码 → 不进 `domains/shared/integrations/workflows/pitfalls`；可进 `decisions` 作为演变记录。
   - 仅代码有但无会话讨论 → 按代码事实写入，不虚构原因。

**分支来源闸门（不可跳过）：**

- `branch_state=session-branch-matches-scan` 只表示会话历史分支与扫描时 Worktree 分支一致；实现是否仍存在，仍须由当前代码或当前 HEAD 验证后才能写入当前状态条目。
- `branch_state=session-branch-differs-from-scan` 明确属于另一分支：该会话的实现声明只能写 `decisions`/演变记录或待验证候选；即使当前代码后来已有同样实现，也必须以当前代码/HEAD 为事实源，不能把会话本身当作当前实现证据。
- `branch_state=unknown-branch` 默认隔离。除非当前代码、当前分支历史或另一条独立证据能复核，否则不把会话结论当作当前事实，并在报告中标记“分支归属未确认”。扫描时 Worktree 的分支只是发现时快照，不能反推会话发生时的分支；若当前代码独立复核通过，仍以代码信号写入当前事实。
- `head_state=session-head-differs-from-scan` 表示会话记录的是同名分支上的旧提交；它不能证明当前工作树仍有该实现。若 `head_state=unknown-head`，同样按保守规则处理。
- 当前事实的唯一落点是“当前代码 + 当前 HEAD 历史”；会话负责补充原因、决策和失败尝试，不负责单独宣布某个实现已经存在。

**子代理任务模板：**

```text
按该来源格式从头到尾完整阅读会话，不要跳跃。

第一步：用文件编辑/补丁路径、tool cwd、git diff/状态等强证据，判断会话是否涉及 `<ROOT>`。
Worktree 绝对路径先映射成“Worktree 根 + 相对路径”，再映射回当前 `<ROOT>`。
不涉及 → 返回“无关 + 一句证据”，停止。

涉及 → 输出 references/session-sources.md 规定的归一化摘要，尤其提取：
- 改动任务、涉及文件、最终结果（已落地/未合并/已回滚/仅讨论/无法确认）
- pitfalls：bug、错误假设、根因、时序/并发/平台坑
- decisions：为什么选 A 不选 B、被否方案、权衡
- workflows：跨文件步骤与易漏点

只输出语义结论和 `<ROOT>` 相对路径；不要照抄原始消息、thinking、密钥、用户名或绝对路径。
```

**边界情况：**

- 所有来源都为空 → 跳过会话提取，只做 3.2，并明确报告“本机未发现可读的项目会话”。
- 会话数量极多 → 全部处理，接受成本；可以按来源/Worktree 分批并行。
- 当前会话仍在写入 → 以当前运行时上下文为准，并按 session id 排除落盘副本的重复摘要。
- 不稳定数据库、protobuf 或云端会话无法自动读取 → 明确报告并请用户导出 JSON/Markdown 后用 `--source` 加入，不静默遗漏。

### 3.4 条目互链规则

- 每个条目末尾有 `## 关联条目` 区域
- 使用相对路径链接：`[条目名](../分类/文件名.md)`
- 只链接真正相关的条目，不要为了链接而链接
- 双向链接：A 链接 B，B 也应链接 A

### 3.5 跨分类边界规则

同一主题可能出现在多个分类中，必须明确边界避免重叠：

| 分类 | 职责 | 示例 |
|------|------|------|
| `domains/` | **What** — 业务做了什么，数据怎么流转 | "游戏追踪系统记录对局数据" |
| `decisions/` | **Why** — 为什么选这个方案 | "为什么选 Tauri 而不是 Electron" |
| `integrations/` | **How** — 怎么集成的，API 和限制 | "Tauri 的文件监听和 IPC 怎么用" |
| `shared/` | **With what** — 用了什么公共工具 | "缓存系统的接口和使用方式" |
| `workflows/` | **How to** — 跨模块操作步骤 | "新增一种卡牌类型要改哪些文件" |
| `pitfalls/` | **Watch out** — 别踩什么坑 | "WebSocket Hook 断连重试的陷阱" |

**如果发现一个条目同时在讲 why 和 how，拆成 decision + integration 两个条目互相链接。**

**接口契约速查 vs shared/integrations 的边界**:

| 层 | 内容 | 位置 |
|---|---|---|
| **有什么**(目录索引) | 接口名 + 一句话职责 + 入口位置 | INDEX.md "## 接口契约速查"区(跨技术栈汇总) |
| **怎么用**(详情) | 用法、约束、踩坑、版本演变 | `shared/<bridge>.md`、`integrations/<service>.md` |
| **完整签名**(权威源) | 参数类型、返回值、错误码 | 代码本身(commands.rs / cloudfunctions/.../index.obj.js / *.proto / openapi.yaml) |

三者**不重叠**:速查跳详情,详情跳代码,各司其职。**接口签名永远不在知识库里抄一份**——避免跟代码脱节。

**项目文档导航 vs 自动生成条目的边界**:

| 来源 | 内容 | 谁维护 |
|---|---|---|
| **项目文档**(README/CLAUDE/docs/*) | 作者人写,跟代码一起更新 | 项目作者 |
| **knowledge/ 条目**(domains/shared/...) | LLM 基于代码合成 | codewise |

**两者互补,不重叠**:
- 项目文档导航 = "**项目作者写过哪些权威文档**"(只列+简介+链接)
- knowledge/ 条目 = "**跨多文件的统一视角**"(LLM 合成的内容)

简介可以提到"另见 domains/X 条目"做相互索引,但**不抄文档内容到 knowledge/ 条目**——文档自带链接即可。

### 3.6 / 3.7 主动识别 pitfalls 与 decisions

完整的信号扫描清单、交叉验证规则、独立成条原则与条目最低信息量要求，见 [references/signal-extraction.md](references/signal-extraction.md)——**3.2 的子代理任务里就要并发扫描这些信号**，不是事后补。

两条不可退让的约束：

- **偏向记录而非遗漏。** 拿不准时默认写入。会话里明明修了 bug 却没产出任何 pitfalls 条目，应视为异常，回头重扫。
- **一个陷阱一个条目。** 不要把多个不相关的坑打包进一个文件——打包等于让后来的人读不到自己需要的那一条。

## Phase 4：索引

生成 `<KBR>/INDEX.md`。**末尾必须写入元信息区**（`codewise_version`、`baseline_commit`、`synced_at`、`scope_root`、`anchor_kind`、`multi_codetree`、`session_sources`、`worktree_count`、`known_worktrees`），增量更新依赖此信息计算 git 差量与会话边界，后几项披露最近一次会话扫描的覆盖范围。若不是 git 仓库，`baseline_commit` 写 `null`。分支归属由目录结构保证，因此不需要漂移检测字段。

`synced_at` **必须用完整 ISO 8601 时间戳**（精确到秒，含时区），不要只写日期——Phase U 的会话边界判断依赖这个精度。

INDEX.md 的完整模板（各分区结构 + 末尾同步元信息区的全部字段定义）见 [references/index-template.md](references/index-template.md)，生成前完整读取。

**元信息区必须写在文件末尾的 `codewise-meta` 标签内**，增量更新依赖它计算 git 差量、判定会话提取边界，以及回传 `known_worktrees`。缺失或被手工改坏会导致下次 update 退化成全量重扫。

**空分类不出现在 INDEX.md 中。**

## Phase 5：验证

1. 检查所有互链是否有效（目标文件存在）
2. 检查 INDEX.md 中的链接是否完整
3. 检查会话扫描报告是否覆盖所有当前 Worktree，并列出检测到但未解析的来源
4. 向用户报告：生成了多少条目、多少互链、会话来源/数量、Worktree 数、是否有断链

## Phase 6：注册到 Agent 指引

同时维护 `<ROOT>/AGENTS.md`（Codex 与通用 Agent）和 `<ROOT>/CLAUDE.md`（Claude Code），让不同客户端都能在新任务开始时发现知识库。只修改 `<ROOT>` 内的文件；若 `<ROOT>` 是子项目，不修改仓库根的同名文件。

### 每个目标文件的写入策略

1. 文件不存在 → 创建并写入标准模板。
2. 文件存在且有 `codewise-registry` 标签 → 只替换标签内内容，标签外一字不动。
3. `CLAUDE.md` 仍使用旧的 `codewise-claude-registry` 标签 → 原位迁移为新标签，不重复追加。
4. 文件存在、无标签、也无用户手写的“知识库/knowledge”段 → 在末尾追加标准模板。
5. 文件存在、无标签、但已有用户自定义知识库段 → 停下询问用户是保留原段还是迁移；不得擅自覆盖。

### 标准模板

```markdown
<!-- codewise-registry:start -->
## 📚 知识库

知识库由 **codewise** skill 生成,不在工作树内。入口:

```bash
# 主线
"$(git rev-parse --path-format=absolute --git-common-dir)"/codewise/<SCOPE>/main/INDEX.md
# 当前分支若有独立知识库,则改读 branches/<分支 slug>/INDEX.md;没有则读主线那份
# (此时它是主线视角,不含本分支改动)
```

**任务起手式(硬约束)**:每个新任务第一步读取 INDEX.md(本会话已读过则跳过)。**不读 = 默认从零摸索 = 重复踩前人已经记录过的坑**。

维护:`codewise update` 增量更新 | `codewise rebuild` 强制重建 | `codewise refresh-docs` 局部刷文档导航 | `codewise refresh-interfaces` 局部刷接口速查（按当前客户端使用 `/codewise`、`$codewise` 或自然语言调用）。**禁止手编知识库目录**——它是 codewise 单源生成的领地。
<!-- codewise-registry:end -->
```

### 关键设计

- **HTML 标签界定**:跟 INDEX 的 `codewise-{docs,interfaces,meta}:start/end` 同套路，update 时机械替换，不污染用户内容。
- **双文件内容保持一致**:`AGENTS.md` 和 `CLAUDE.md` 的标签块使用同一模板，避免不同 Agent 获得不同规则。
- **不抄 INDEX 内容到指引文件**:指引文件只负责让 Agent 真正读取 INDEX。

**这一步是必须的。** 只写 `CLAUDE.md` 会让 Codex 看不到注册规则，只写 `AGENTS.md` 则无法覆盖 Claude Code。

---

## Phase U：增量更新

当知识库已存在，按以下顺序执行。

**前置**：Phase 0 已读取 INDEX.md 元信息区，拿到 `baseline_commit`。git 仓库 + baseline 存在 → 走 git 差量主路径；否则退化兜底（询问用户 / mtime / 仅会话回顾）。

### U.1 git 时间线采集（主流程，轻量）

仅在 git 仓库且 baseline 存在时执行。**主流程只读元信息和清单，不读完整 diff**——完整 diff 留给 U.5 子代理。

```bash
# baseline 被 rebase/分叉时不要直接拿它与 HEAD 做差量；先确定共同祖先。
current_head=$(git rev-parse HEAD)
comparison_base=$(git merge-base <baseline_commit> "$current_head" || true)

# 没有共同祖先时只能报告“无法比较”，不要执行形如 `..HEAD` 的空范围。
if [ -n "$comparison_base" ]; then
  # 演变路径：取完整 commit body，不是 --oneline。标题往往只写“做了什么”，
  # 而“为什么”写在 body 里——对本机没有会话记录的改动，body 是唯一的根因来源。
  git log "$comparison_base"..HEAD --format='%h%n%s%n%b%n--' -- <ROOT>

  # merge commit 的描述常常概括整条分支的意图
  git log "$comparison_base"..HEAD --merges --format='%h %s%n%b' -- <ROOT>

  # 热点文件：变更行数统计
  git diff "$comparison_base"..HEAD --stat -- <ROOT>

  # 变更文件清单：U.5 分组依据
  git diff "$comparison_base"..HEAD --name-only -- <ROOT>
else
  echo "无法计算 baseline 与当前 HEAD 的共同祖先；跳过 git 差量，改走兜底策略。"
fi
```

若 `comparison_base` 与 `baseline_commit` 不同，报告“baseline 不在当前分支历史中，已用 merge-base 比较”（通常意味着发生过 rebase 或 squash）；不得把旧分支独有的事实自动带入当前状态。

**非会话证据补采（当本机没有对应会话时必做）：** 改动来自其他人、其他机器，或未保存在本机的 Agent 时，会话扫描会空手而归——而根因恰恰只在那些会话里。此时尽力从以下来源抢救：

- commit body 里的 `Why:` / `Fixes:` / `Refs:` 段落与 issue/PR 编号
- merge commit 描述（整条分支的意图概括）
- `<ROOT>` 内的 CHANGELOG / release notes 在该区间的条目
- 代码注释里解释"为什么"的部分（不是解释"做了什么"的）

这些是低保真替代品，不能等同于会话记录。**据此产出的条目一律标 `evidence: code-only`**，并在 U.6 报告里单列。

主流程产出两份摘要供后续步骤复用：
- **演变摘要**：commit messages（含 body）串起来，理解这段时间项目在做什么以及为什么
- **变更文件分组**：按功能域 / 顶层目录把变更文件分组，每组将分配一个子代理

### U.1.5 Worktree 时间线采集（轻量、只作候选）

运行 `scripts/discover_sessions.py` 得到的 `project.worktrees[]` 是本项目的完整 Worktree 集合。**跳过 `stale=true` 的条目**——它们已不在注册表中、目录可能已删除，没有 HEAD 可比较，只用于会话路径匹配。除当前 Worktree 外，对每个活跃 Worktree 只采集：

```bash
# 每个 Worktree 都以自己的 HEAD 计算共同祖先；不能把同一个全局 baseline
# 直接与所有分叉分支比较，否则会把反向差异/删除误判成当前改动。
branch_base=$(git -C <worktree-root> merge-base <baseline_commit> HEAD)
git -C <worktree-root> log "$branch_base"..HEAD --oneline -- <mapped-scope>
git -C <worktree-root> diff "$branch_base"..HEAD --name-only -- <mapped-scope>
# 未提交改动必须单独采集，不能混入 branch_base..HEAD 的提交差量。
git -C <worktree-root> diff --name-only -- <mapped-scope>
git -C <worktree-root> diff --cached --name-only -- <mapped-scope>
git -C <worktree-root> status --short -- <mapped-scope>
```

这些结果用于解释其他 Worktree 会话和识别“未合并/未提交/已回滚”。**它们不是当前 `<ROOT>` 的事实源，也不进入 U.5 的当前条目更新文件清单。** 只有在当前代码或当前分支历史验证后，相关实现才能写成现状。没有 `merge-base` 的 Worktree 只报告为无法比较，不得猜测其变更范围。

### U.2 当前会话回顾（必做）

**完整阅读本次会话的全部消息**——从第一条到触发 update 的这一刻,**不应用 timestamp 过滤**。语义连续性优先于"避免重读":人类的记忆会压缩细节,模型的注意力会偏向近期消息,调试早期的关键线索(最初的报错、被排除的假设、中途的误判)往往就在这时被丢掉,而这些恰恰是 pitfalls 的核心原料。

**baseline 是"提取边界",不是"读什么的过滤线":**
- 完整通读会话(获得完整语义上下文,理解后文对前文的引用)
- 但**只为 `synced_at` 之后的讨论产出/更新条目**——`synced_at` 之前的部分已被上次 update 处理过,不再重复产出条目
- 例外:若 `synced_at` 之后的讨论引用了之前的某个根因或决策,可回头看前文确认理解,但不要为前文重复产出条目

**主动扫描以下信号，命中任何一条就产出或更新条目：**

| 信号 | 去向 | 提取要点 |
|------|------|----------|
| 用户报告过一个 bug / 报错 / 异常行为 | `pitfalls/` | 现象、根因、误导性表象、规避方式 |
| 调试过程中排除过某个假设（"一开始以为是 X，其实是 Y"） | `pitfalls/` | Y 是什么，为什么 X 看起来像但不是 |
| 某个修复涉及时序、并发、边界、隐式依赖、平台差异 | `pitfalls/` | 触发条件 + 修复姿势 |
| 讨论过"为什么选 A 不选 B" / 否决过某个方案 | `decisions/` | 被否方案、否决原因 |
| 摸索出跨文件/跨模块的操作步骤 | `workflows/` | 完整步骤 + 易漏点 |
| 发现代码与文档/直觉不符 | `pitfalls/` | 实际行为 vs 预期 |
| 改动发生在其他 Worktree | 先校验状态 | Worktree、相对路径、已合并/未合并/已回滚 |

**偏向记录而非遗漏。** 拿不准时默认写入，宁可条目稍多也不要漏掉根因。空手而归（会话里明明修了 bug 却没产出任何 pitfalls 条目）应视为异常，回头再扫一遍会话历史。

**pitfalls 条目的最低信息量：** 现象、根因（代码看不出的那部分）、修复/规避。三者缺一则继续向用户追问补齐，不要用"修了 XX 文件"这种描述代替根因。

**只提取代码里看不出来的信息。** 代码改了什么、文件结构怎么变的，这些 git diff 已经覆盖——但 git diff **看不到**的根因分析、误判过程、为什么这么改，必须从会话里抢救出来。

**【隐私边界】** 写入条目时只输出语义结论(根因、决策、陷阱),**不要照抄原始用户消息片段、密钥、含用户名的绝对路径**。条目会被 git 追踪。

### U.2.5 跨会话扫描(在 baseline 之后有活动的其他会话)

只读当前会话不够——同一个项目的根因和决策可能散落在多个并行/历史会话里。**必须扫描项目相关的其他会话**,不然每次 update 都只能看到触发 update 那个会话的视角。

**操作步骤：**

1. 完整读取 [references/session-sources.md](references/session-sources.md)。
2. 用 `synced_at` 运行统一发现脚本，并回传上次记录的 Worktree 集合：

   ```bash
   python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> \
     --since '<synced_at>' --baseline '<baseline_commit>' \
     --known-worktree '<INDEX.known_worktrees 的每一项>' --pretty
   ```

   条目多时改用 `--known-worktrees-file <临时清单>`（一行一个）。**漏传等于丢掉那些 Worktree 的全部会话。**

3. 按 `provider + session_id` 排除当前会话的落盘副本；其余每个候选会话启动一个子代理。
4. 子代理从头到尾阅读会话以保留语义连续性，但只为消息时间 `timestamp > synced_at` 的讨论产出新信号；无可靠消息时间的来源用文件 mtime 作为保守边界并标注。
5. 先做 scope 内容过滤，再输出统一归一化摘要；同时标明对应 Worktree 及改动状态。
6. **`multi_branch=true` 的会话必须分段**：按 `branches` 与每条消息（Codex 按每个 resume 标记点）把信号拆到各自所属分支，逐段定性。整条会话用一个分支值定性会把大部分信号贴错标签。
7. 主流程汇总后进入 U.3，与当前会话、当前分支 git 时间线、Worktree 时间线一起交叉验证。

会话的分支证据是 U.3 定性的输入，不是结论：**分支名不同 ≠ 未合并**。合并状态判定链、`unmerged` 的两种相反处理（分支仍在 → 隔离；分支已删 → 采纳因果断言为已否决方案）与断言分类，全部见 [references/branch-resolution.md](references/branch-resolution.md)。当前 Worktree 的扫描分支不能替代会话历史分支。

**为什么 mtime 只作粗筛？** 消息级硬切读取会破坏上下文；数据库 mtime 也不能代表单条会话时间。必须完整读、理解上下文，再用 `synced_at` 控制“哪些讨论产生新条目”。

**边界情况：**

- 所有来源为空 → 跳过 U.2.5，只用 U.1/U.1.5 + U.2 + U.3 继续。
- 会话讨论的代码已重构、未合并或已回滚 → 不写成当前实现，可进入 `decisions/` 作演变记录。
- 检测到但无法解析的来源 → 报告来源与导出建议，不得静默当成“无会话”。

### U.3 交叉验证 git ↔ 会话

**第一步：定性。** 对 U.2 + U.2.5 提取出的每个信号，按 [references/branch-resolution.md](references/branch-resolution.md) 做**信号级**归属与合并状态判定——不是按整个会话定性。跨分支续写的会话必须先分段，`merged-likely` 必须走完二次验证。

**第二步：按断言类型分流。** 这是整套判定的地基：

| 断言类型 | 采纳规则 |
|---|---|
| **状态断言**（"X 已修好"、"架构是 Y"） | 必须由当前代码验证，会话说了不算 |
| **因果断言**（"根因是 Z"、"试过 A 因 B 失败"、"选 C 不选 D"） | 代码里验证不了，随合并状态采纳 |

**不要用一条规则同时处理两类。** 说"只采纳代码事实、不采纳会话断言"等于把根因分析全部丢掉——而那正是 codewise 唯一无法从 `git diff` 得到的东西。

**第三步：把 U.1 的演变摘要和会话提取放在一起对比**，捕捉以下信号：

- **会话讨论过 X 修复，但 git 里没动 X** → 可能是讨论但没做、在另一分支、或被回滚；追问用户
- **git 大改了 Y，会话里没提** → Y 可能来自其他人或其他机器；按 U.1 的非会话证据（完整 commit body、PR 引用、CHANGELOG）更新条目，并标记 `evidence: code-only`
- **commit message 透露的意图与会话根因不一致** → **以会话为准**，commit message 经常省略真实原因
- **同一根因被多个会话（不同来源/会话）印证** → 高置信度，值得入条目；只在一处出现的可下调 `confidence`
- **会话宣称已修复，但当前代码没有** → 走 `unmerged` 分支：分支仍在则隔离；分支已删则因果断言进 `decisions/` 标为已否决方案
- **`head_state` 显示会话记录的是同名分支上的旧提交** → 因果断言仍可采纳，但当前实现状态必须重新由当前代码验证
- **baseline 不再是当前 HEAD 祖先** → 以 `merge-base` 结果为差量起点，并在更新报告中披露发生过 rebase/squash

矛盾不必全部当场解决，但要带入 U.5——它们影响子代理的分发策略和条目内容侧重。

### U.4 影响评估

基于 U.1 变更文件清单 + U.2/U.2.5 会话提取 + U.3 交叉验证，读取 `<KBR>/INDEX.md` 和相关条目，判断：
- 哪些现有条目需要更新？（变更文件命中条目的"关键文件"列表）
- 是否需要新增条目？（出现新功能域、新依赖、新 workflow）
- 是否有条目应该删除？（对应模块整体被移除）

#### U.4.1 文档变更专项规则 ⭐

变更文件清单里的 `.md` 文件单独识别处理(不走 U.5 子代理流程):

```
变更文件清单中提取所有 .md 路径(知识库已不在工作树内,无需排除):
  · README.md / CLAUDE.md / AGENTS.md / CONTRIBUTING.md / CHANGELOG.md
  · docs/**.md / doc/**.md / design/**.md / specs/**.md / architecture/**.md

按 git diff 状态分类:
  · A (Added)    → 主流程完整读新文档,在"项目文档导航"区加新条目
  · M (Modified) → 主流程重读完整文档,更新对应简介
  · D (Deleted)  → 主流程从"项目文档导航"区移除该文档条目
  · R (Renamed)  → 移除旧条目 + 加新条目
```

**主流程直接处理,不派子代理**——文档量小(通常 ≤30 个),主 context 装得下。完整重读文档(不要只看 diff),写新简介 50-100 字(同 Phase 1.1 规则)。

#### U.4.2 接口契约变更专项规则

如果变更文件清单包含:
- `<ROOT>/src-tauri/src/**.rs` 中含 `#[tauri::command]` 的文件
- `<ROOT>/cloudfunctions/**` 或 `<ROOT>/uniCloud-*/cloudfunctions/**` 下任意改动
- `<ROOT>/**/*.proto` / `<ROOT>/**/*.graphql` / `<ROOT>/**/openapi.yaml`
- `<ROOT>/**/*.schema.json` 或 `<ROOT>/prisma/schema.prisma`

→ **重新跑 Phase 1.0 Step 4.5 接口契约抽取**,刷新 INDEX 的"## 接口契约速查"区。

主流程直接处理,机械替换 `<!-- codewise-interfaces:start --> ... <!-- codewise-interfaces:end -->` 之间内容。

### U.5 执行更新（子代理分发完整 diff）

**核心原则：主流程不读完整 diff。** 按 U.1 的“变更文件分组”为每组启动一个子代理，子代理读自己负责文件的完整 diff，主流程只汇总。这样每一行 diff 都被读到，且不会挤爆主上下文。

每个子代理的任务模板：

```
本组负责的变更文件：
[文件清单]

任务：
1. 读取这些文件当前的完整内容（`<ROOT>` 内的实际代码）
2. 读取这些文件从 baseline 到 HEAD 的完整 diff：
   git diff <comparison_base>..HEAD -- [文件清单]
3. 对照主流程指出的可能受影响条目，决定：
   - 哪些条目需要更新（架构、关键文件、流程描述）
   - 是否需要新增条目
   - **更新前先读取条目当前内容，在其基础上修改，不要从零重写**——用户可能手动编辑过条目，保留其修改，只追加或更新变化部分
4. 如果 diff 中体现了"代码看不出意图"的修改（奇怪的修复、绕行写法、特殊处理），结合主流程提供的会话摘要，写入或更新对应的 pitfalls/ 条目

【隐私边界】写入条目时只输出语义结论(根因、决策、陷阱、文件路径用 `<ROOT>` 相对路径),
**不要照抄密钥、含用户名的绝对路径、原始用户消息片段**。条目会被 git 追踪。

严格只读 `<ROOT>` 内的文件。
```

主流程分发时附带：U.2 + U.2.5 的会话提取结论 + U.3 的交叉验证发现 + 该分组涉及的现有条目列表。

子代理返回各自更新方案后，主流程统一应用，避免冲突。

### U.6 写回新 baseline 与索引

**原子性原则:baseline 必须最后写入。** 顺序固定:

1. 更新受影响的条目文件
2. 更新 INDEX.md(新增/删除条目)
3. 检查互链完整性(同 Phase 5)
4. 确认 `<ROOT>/AGENTS.md` 与 `<ROOT>/CLAUDE.md` 中的知识库指引仍然存在
5. **最后**才更新 INDEX.md 元信息区：`baseline_commit` 改为当前 `git rev-parse HEAD`，`synced_at` 改为当前完整 ISO 8601 时间戳，并刷新 `session_sources`、`worktree_count` 与 `known_worktrees`（后者原样写入发现脚本输出的 `project.known_worktrees`，只增不减）
6. 提交知识库仓库并推送本地镜像（见 [references/storage-layout.md](references/storage-layout.md)）；GitHub 推送失败只报告，不阻塞

**为什么要这个顺序?** 如果中途崩了,baseline 没更新,下次 update 仍然从旧 baseline 算 diff——会重复处理这次没改完的部分,但不会丢东西。**重跑是安全的**。

向用户报告(末尾输出):

```
✓ 更新完成
  · 条目变更: 更新 N、新增 M、删除 K
  · 旧 baseline: <8 位短码>(synced_at: <旧时间>)
  · 新 baseline: <8 位短码>(synced_at: <新时间>)
  · 下次 update 将从 <新 baseline 短码> 开始计算 diff
  · 会话来源: <provider:数量 ...>；跨分支会话 <N> 条已分段归属
  · 知识库仓库: 本地镜像已更新；远端 <已同步 | 未同步，N 个 commit 待推送>

  ⚠ 只有代码证据、根因缺失（evidence: code-only）:
    · <模块/条目> — 改动来自 <commit 短码/作者>，本机无对应会话
    → 这些条目只能描述"改了什么"，"为什么"需要向对应作者确认

  ⚠ 待确认（confidence: medium）:
    · <条目> — <为何未达 confirmed，一句话>
```

**`code-only` 清单必须单独列出来，不能混在正常条目变更里。** 静默降级会让人以为知识库覆盖完整——而实际上那部分只有"改了什么"，没有"为什么"，恰恰缺了知识库最有价值的部分。没有这类条目时整段省略。

让用户清楚下次的起点在哪,而不是看着 INDEX.md 末尾的元信息自己猜。

---

## Phase R:局部刷新

适用于"只想刷新 INDEX 某个机械抽取区域"的场景——成本极低,不需要跑完整 Phase U。

### 触发方式

- `/codewise refresh-docs` → 只刷新"## 项目文档导航"区
- `/codewise refresh-interfaces` → 只刷新"## 接口契约速查"区

### 适用场景

- 你新加了一个 docs/X.md → `refresh-docs` 立即让 INDEX 包含
- 你修改了 docs/server-setup.md → `refresh-docs` 让简介反映新内容
- 你新加了一个 Tauri command → `refresh-interfaces` 立即让接口速查表包含
- 你只想看导航区长什么样,不想跑完整 update

**前置条件**:`<KBR>/INDEX.md` 已存在(否则走首次生成,不是 refresh)。

### Phase R 流程

```
R.1 模式识别
    /codewise refresh-docs       → 走 docs 路径
    /codewise refresh-interfaces → 走 interfaces 路径

R.2 跑对应抽取
    docs       → 跑 Phase 1.0 Step 4.6 + Phase 1.1 完整读 + 写简介
    interfaces → 跑 Phase 1.0 Step 4.5 接口契约抽取

R.3 机械替换 INDEX 对应区
    用 sed 或 Read+Edit 找到 <!-- codewise-{docs,interfaces}:start --> 和
    对应的 :end 标签,**只替换两个标签之间的内容**,不动 INDEX 其他区域

R.4 报告
    向用户报告:刷新了哪些条目(新增/修改/删除),不更新 baseline_commit
```

### 关键约束

- ✅ **不更新 `baseline_commit` 和 `synced_at`** ——refresh 不是完整同步,跟 git baseline 解耦
- ✅ **不动其他条目**——domains/shared/decisions 等一字不改
- ✅ **不依赖 git baseline**——直接扫当前 working directory(允许未 commit 的改动)
- ❌ **不替代 update**——refresh 只刷机械抽取区,代码变更对应的条目仍然要 update 才能更新

### 一个边界提醒

如果 `<ROOT>` 不是 git 仓库,update 会有问题但 refresh 仍然能跑——因为 refresh 不依赖 git baseline。这是 refresh 的额外优势。

---

## Phase M：并入分支知识库

长功能分支的代码合并回主线后，把它的知识也并进来。**完整读取 [references/merge-protocol.md](references/merge-protocol.md) 再执行。**

**这不是 rebuild。** 分支通常只改动一小部分模块，其余条目两边完全相同——用 `_meta.json` 里记录的 `forked_from` 作 base 做三方合并，AI 只需要处理真冲突的那几条。

流程骨架：

1. 确认 `branches/<slug>/` 存在，读 `_meta.json` 拿到原始分支名与 `forked_from`
2. 确认代码侧已合并（`git merge-base --is-ancestor <branch> HEAD`）；未合并则停下询问——知识不该超前于代码
3. 对分支目录里的每个文件做条目级三方合并；`_deleted` 清单按删除理由分别处理
4. 跨路径语义去重（只扫本次新引入的条目，粗筛后再判断，**判定重复也不自动合并**）
5. 重建 `main/INDEX.md`，检查互链完整性
6. `branches/<slug>/` 移入 `archive/`；分支是废弃而非合并的，其因果断言先按 `unmerged` + 分支已删规则进 `main/decisions/` 标 `rejected`
7. 提交知识库仓库并推送本地镜像

**因果类条目（`pitfalls`/`decisions`）的合并语义是并集**——一个坑不会因为另一条分支没记录就不成立。只有状态类条目才可能真冲突，且以合并后的当前代码裁决。

---

## 条目模板

六类条目（`domains/`、`shared/`、`decisions/`、`integrations/`、`workflows/`、`pitfalls/`）的完整结构模板见 [references/entry-templates.md](references/entry-templates.md)，生成条目前完整读取。
## 质量标准

- **条目独立可读**：不依赖其他条目也能理解
- **互链有意义**：只链接真正相关的条目
- **粒度适中**：一个条目 30-80 行，超过 100 行就拆分
- **不重复代码注释**：知识库记录的是代码里看不出来的信息
- **保持更新**：过时的知识库比没有更糟

---
name: codewise
description: 项目级记忆库生成与维护 — 扫描代码库 + 项目相关的 Claude Code 会话历史，生成网状互链的模块化知识库（docs/knowledge/），帮助人和 AI 快速理解项目。支持首次生成（/codewise）、增量更新（/codewise 或 /codewise update）和强制重建（/codewise rebuild）。包含多代码树检测(自动识别 src-tauri/、uniCloud-*/、packages/*/ 等)、接口契约速查(让 AI 一进项目掌握所有对外接口)、pitfalls 信号扩展+交叉验证、架构整体观必出等强化。Use when user says "codewise", "/codewise", "knowledge", "生成知识库", "更新知识库", "重建知识库", "document the project", "create knowledge base", or when onboarding to a new project and need structured documentation.
argument-hint: "[update|rebuild] [path] [scope description]"
---

# 项目知识库

扫描代码库，生成网状互链的模块化知识库。AI 读 INDEX.md 按需跳转，不挤上下文。

**核心原则：Opus 编排，Sonnet 读码。** Opus 不直接读代码文件，所有代码分析委托给 Explore 子代理。

## 目标

$ARGUMENTS

---

## Phase 0：解析 scope

扫描 `$ARGUMENTS`，提取第一个**实际存在的目录路径**作为 `<ROOT>`（子项目根）；其余文字作为 scope description 保留给后续阶段。若未提供路径，`<ROOT>` 默认为 `.`（整个仓库，行为与旧版一致）。

**之后所有路径占位符都相对 `<ROOT>` 解析**：
- 知识库输出位置：`<ROOT>/docs/knowledge/`
- 注册指引写入：`<ROOT>/CLAUDE.md`
- Phase 1 读文档、Phase 1.2 子代理扫描范围、Phase U 的 `git diff` pathspec，全部限定在 `<ROOT>` 内
- 子项目外的文件不读、不扫、不链接。仓库根的 CLAUDE.md 可作为外层上下文参考，但不作为事实源，也不修改

**关于会话 jsonl 路径(Phase 3 全项目会话扫描 + Phase U.2.5 跨会话扫描会用到):** Claude Code 会话文件存在 `~/.claude/projects/<sanitized-cwd>/` 下,目录名是**用户当时启动 Claude Code 的 cwd**(路径中的 `/` 替换成 `-`),**不一定等于 `<ROOT>`**。比如用户在仓库根启动 Claude Code 但 `<ROOT>` 是子目录,jsonl 仍在仓库根对应的目录里。

定位策略:
- 取当前 Claude Code 进程的 cwd(即用户启动时的工作目录),换算成 `~/.claude/projects/<sanitized-cwd>/`
- 该目录下的 jsonl **包含跨多个子项目的会话内容**,不能假设"目录里的 jsonl 都和 `<ROOT>` 相关"
- 必须用**会话内容里出现的文件路径**做交叉过滤(比如 jsonl 里提到的 file_path、Bash 命令的 cwd 等),只保留涉及 `<ROOT>` 内文件的会话

**确认 `<ROOT>`：** 开始 Phase 1 前，先向用户明确告知解析结果（例如"将为 `apps/web/` 生成知识库，输出到 `apps/web/docs/knowledge/`"），等待确认后再继续。

**过期检测（仅当 INDEX.md 已存在 + 是 git 仓库时执行）：** 确认 `<ROOT>` 后，读 `<ROOT>/docs/knowledge/INDEX.md` 末尾的元信息区，提取 `baseline_commit`。若存在，执行：

```bash
git rev-list --count <baseline_commit>..HEAD -- <ROOT>
```

将累计 commit 数和 `synced_at` 一并告知用户（例如"距上次同步 2026-04-15 累计 23 个 commits"）。**这只是信息披露，不阻塞流程。** 若 `baseline_commit` 不存在（旧版生成的知识库）、commit 已被 rebase 冲掉、或非 git 仓库，提示原因并继续——后续 Phase U 会按兜底策略走（询问用户或 mtime）。

---

## 模式判断

检查 `<ROOT>/docs/knowledge/INDEX.md` 是否存在：

- **不存在** → 首次生成（Phase 1-6，包含代码全文件精读 + 项目相关会话全扫）
- **存在 + 无参数** → **默认增量更新（Phase U）**
- **存在 + 参数含 `update`** → 增量更新（Phase U，等价于无参数）
- **存在 + 参数含 `rebuild`** → 强制重建（重新执行首次生成流程，会覆盖现有条目；**必须向用户明确告知并等待确认后再执行**）
- **存在 + 参数含 `refresh-docs`** → **局部刷新 INDEX 的"项目文档导航"区**(Phase R,只跑 Step 4.6 + Phase 1.1 文档简介,不动其他条目,不更新 baseline_commit)
- **存在 + 参数含 `refresh-interfaces`** → **局部刷新 INDEX 的"接口契约速查"区**(Phase R,只跑 Step 4.5,同上不动其他)

**重建路径必须显式触发，默认走 update。** 这是为了保护用户手动编辑过的条目，避免误覆盖。

**`refresh-*` 路径用于局部场景**(只改了文档/只加了 Tauri command,想立刻刷 INDEX 不想跑完整 update)。详见 Phase R。

**没有"快速版"模式。** 我们的定位是"项目级记忆库",首次建立 baseline 就要完整——代码 + 会话历史一次消化干净。如果项目极大导致首次成本过高,接受这个成本,或缩小 `<ROOT>` 范围分子项目跑。

---

## Phase 1：探测

先用 **Phase 1.0 机械检测代码树和扩展名清单**,再读已有文档,最后子代理基于事实清单做归类分析。

### 1.0 多代码树检测 + 动态扩展名推断

**主流程必须真做这步——把"找代码树"从"子代理凭印象扫描"变成"主流程机械检测"。**

#### Step 1: 检测代码树根目录

主流程跑 Bash 检测 `<ROOT>` 内可能存在的多个代码树:

```bash
# 主代码树
ls -d <ROOT>/src 2>/dev/null
ls -d <ROOT>/lib 2>/dev/null
ls -d <ROOT>/app 2>/dev/null

# 后端/桌面/云函数代码树(常见于全栈项目)
ls -d <ROOT>/src-tauri 2>/dev/null              # Tauri 桌面端 Rust
ls -d <ROOT>/uniCloud-* 2>/dev/null             # uni-app 云函数
ls -d <ROOT>/cloudfunctions 2>/dev/null         # 微信云开发
ls -d <ROOT>/functions 2>/dev/null              # Firebase / Vercel
ls -d <ROOT>/server <ROOT>/backend <ROOT>/api 2>/dev/null  # 通用后端

# Monorepo 子包(注意:遇到这种情况优先建议用户给具体子包路径)
ls -d <ROOT>/packages/*/src 2>/dev/null
ls -d <ROOT>/apps/*/src 2>/dev/null
```

存在的目录都是**有效代码树**。所有树都要被覆盖,**不能假设单一 src 入口**。

#### Step 2: monorepo 检测

```bash
ls <ROOT>/pnpm-workspace.yaml <ROOT>/lerna.json 2>/dev/null
grep -l '"workspaces"' <ROOT>/package.json 2>/dev/null
```

如果检测到 monorepo + `<ROOT>` 是仓库根,**停下提示用户**:

> "检测到 monorepo(pnpm-workspace.yaml / lerna.json / yarn workspaces)。建议给具体子包路径作为 `<ROOT>`(如 `apps/web` 或 `packages/core`),否则跨子包的 imports 路径风格不一致,扫描质量会下降。继续在仓库根跑请确认。"

等用户决定后再继续。

#### Step 3: 从元文件推断扩展名清单

主流程读取 `<ROOT>` 内的项目元文件,**动态生成**该项目应扫描的扩展名:

```
读 package.json → 加 .js/.jsx/.mjs/.cjs (基础)
  存在 typescript 依赖 → 加 .ts/.tsx
  存在 vue 依赖 → 加 .vue
  存在 svelte → 加 .svelte
  存在 astro → 加 .astro
  存在 unbuild/uni-app/dcloudio → 加 .uvue (uni-app 特有)
  存在 tailwind/unocss/postcss → 加 .css/.scss/.less/.postcss

读 Cargo.toml → 加 .rs
读 go.mod → 加 .go
读 pyproject.toml / setup.py / requirements.txt → 加 .py / .pyi
读 Podfile / Package.swift → 加 .swift / .m / .mm / .h
读 build.gradle / pom.xml → 加 .kt / .java
读 Gemfile → 加 .rb
读 composer.json → 加 .php
读 mix.exs → 加 .ex / .exs

不存在元文件(纯脚本/教学项目):
  抽样 <ROOT> 首层文件的扩展名分布,出现 ≥3 次的加入候选
  
用户在参数中明确指定的扩展名:无条件加入(覆盖优先级最高)
```

#### Step 4: 推断排除规则

```
通用排除: .git、.svn、.hg、.idea、.vscode、coverage、tmp、.cache、*.min.js、*.generated.*、*.gen.*、*-lock.json
JS/TS: node_modules、.next、.nuxt、.turbo、dist、build、out、.vercel、.netlify
Rust: target
Python: __pycache__、.venv、venv、.tox、dist、build、.eggs、*.egg-info
Go: vendor
Swift/iOS: Pods、DerivedData、.build
Java/Kotlin: target、build、out、.gradle
uni-app: uni_modules、unpackage
```

#### Step 4.5: 接口契约抽取

**目的**:让 AI 一进项目就掌握"对外调用入口"的全局地图。云函数项目尤其需要——接口零散在多个 cloudfunctions/<name>/index.obj.js 里,不汇总 AI 找不全。

主流程跑机械抽取脚本(按项目类型,有就跑,无就跳过):

```bash
# Tauri commands
grep -rn '#\[tauri::command\]' <ROOT>/src-tauri/src/ 2>/dev/null

# 云函数(uni-app/微信云开发/Firebase 等)
ls -d <ROOT>/cloudfunctions/*/ <ROOT>/uniCloud-*/cloudfunctions/*/ <ROOT>/functions/*/ 2>/dev/null

# REST endpoints(NestJS 装饰器)
grep -rEn '@(Get|Post|Put|Delete|Patch)\(' <ROOT>/src/ 2>/dev/null

# Express/Koa endpoints
grep -rEn '(app|router)\.(get|post|put|delete|patch)\(' <ROOT>/src/ 2>/dev/null

# GraphQL/gRPC/OpenAPI 契约文件
find <ROOT> -name '*.proto' -o -name '*.graphql' -o -name 'schema.gql' \
  -o -name 'openapi.yaml' -o -name 'swagger.yaml' 2>/dev/null

# 数据库 schema(uniCloud / Prisma)
ls <ROOT>/uniCloud-*/database/*.schema.json <ROOT>/prisma/schema.prisma 2>/dev/null
```

**抽取结果汇总成"接口契约清单"**,作为 Phase 4 INDEX.md "## 接口契约速查"区的输入。

**只汇总三件事**(每个接口):
1. **接口名**(命令名/云函数名/endpoint 路径)
2. **一句话职责**(从代码注释或函数名推断)
3. **入口位置**(代码文件路径)

**不抄完整签名**——签名跟代码同步无法保证(每次 update 间隙仍可能滞后),完整签名让 AI 跳代码看更可靠。

**判定边界**(什么算"对外调用入口"):
- ✅ **跨进程/网络的契约**:Tauri commands、Tauri events、云函数、HTTP endpoints、gRPC、GraphQL
- ✅ **跨语言边界**:Rust pub fn extern、Wasm exports
- ✅ **数据库 schema**(改 schema 破坏所有读写方,算契约)
- ❌ **内部 composable / utility / Pinia store actions**(本项目自己用,改了内部改一遍即可)
- ❌ **JQL 直接调数据库**(SDK 用法,不是命名接口;真正契约是数据库 schema)
- ❌ **组件 props/events**(内部组件不是对外接口,除非项目是 SDK/UI 库)
- ⚠️ **npm 包公开 API**(项目是库时算,是应用时不算)

#### Step 4.6: 项目文档清单抽取 ⭐

**目的**:让 AI 一进项目就知道作者写过哪些权威文档(README / CLAUDE.md / docs/*.md / 设计文档等)。这些**人写文档比知识库自动条目更权威**——作者亲手写,跟代码一起更新。codewise 不能让它们对 AI 隐形。

主流程跑机械抽取,产出文档清单(只列路径,简介在 Phase 1.1 完整读后写):

```bash
# 根目录约定文档
ls <ROOT>/{README,CLAUDE,AGENTS,CONTRIBUTING,CHANGELOG,SECURITY}.md 2>/dev/null

# 项目文档目录
find <ROOT>/{docs,doc,design,specs,architecture} -maxdepth 3 -name '*.md' 2>/dev/null \
  | grep -v '/knowledge/' \
  | grep -v '/node_modules/'
```

**排除规则**:
- ❌ `<ROOT>/docs/knowledge/**`(自己生成,避免循环引用)
- ❌ `<ROOT>/node_modules/**`(第三方)
- ❌ `<ROOT>/dist/`、`<ROOT>/build/`(构建产物)
- ❌ `<ROOT>/.git/`(版本控制元数据)

**清单格式**(供 Phase 1.1 完整读用):

```
[
  "README.md",
  "CLAUDE.md",
  "docs/CODEBASE_MAP.md",
  "docs/server-setup.md",
  "docs/recognition-api.md",
  ...
]
```

**LICENSE 等纯协议文档不抓**——对 AI 理解项目无价值。

#### Step 5: 报告给用户

跑完后向用户报告检测结果:

```
检测结果:
- 有效代码树: [src/, src-tauri/, uniCloud-alipay/, ...]
- 推断扩展名: [.ts, .tsx, .vue, .rs, ...]
- 排除规则: [node_modules, target, ...]
- 监测到的元文件: package.json (deps: vue, typescript, dcloudio), Cargo.toml, ...
- 接口契约抽取: Tauri commands N 个 / 云函数 M 个 / REST endpoints P 个 / 契约文件 K 个
- 项目文档清单: N 个(README/CLAUDE/docs/*),将在 Phase 1.1 完整读后写简介

如有遗漏的代码树/扩展名/接口类型/文档,现在告知;否则继续 Phase 1.1。
```

**这些信息后面会写入**:
- `multi_codetree` 字段(代码树覆盖)
- INDEX.md "## 接口契约速查"区(接口清单)
- INDEX.md "## 项目文档导航"区(文档清单 + 简介)

### 1.1 读取已有文档（直接读，不用子代理，全部限定在 `<ROOT>` 内）：
- `<ROOT>/CLAUDE.md`、`<ROOT>/README.md`、`<ROOT>/AGENTS.md` — 项目说明
- `<ROOT>/package.json`、`<ROOT>/Cargo.toml`、`<ROOT>/go.mod`、`<ROOT>/Podfile`、`<ROOT>/project.yml` 等 — 技术栈和依赖
- `<ROOT>` 下的设计文档、架构文档 — 常见位置如 `docs/`、`doc/`、`design/`、`specs/`
- 仓库根（`<ROOT>` 之上）的 CLAUDE.md 可快速浏览作为外层上下文，但不作为事实源

**已有文档的使用策略：读文档获取意图，读代码验证事实，冲突时以代码为准。**
- 文档中的架构描述、模块划分 → 作为 Phase 2 规划条目的参考输入
- 文档中的设计决策、选型原因 → 直接吸收到 `decisions/` 条目（这类信息代码里看不出来，文档是唯一来源）
- 文档中的技术细节（用了什么、怎么配置）→ 必须用代码验证，项目文档经常过时
- 如果文档和代码矛盾 → 条目以代码为准，可以在条目中标注"文档称 X，实际代码为 Y"

**写"项目文档导航"简介(必做)** ⭐:

对 Phase 1.0 Step 4.6 抽取的**每个文档完整读一遍**(用 Read 工具读全文,不要只看 H1+第一段),然后写一段 50-100 字的简介:

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

**1.2 启动 Explore 子代理(并行)扫描源码,范围严格限定在 Phase 1.0 检测出的代码树清单内:**

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

### 2.2 展示给用户确认

列出每个条目的标题和一句话描述，等待用户确认或调整后再生成。

**不需要的分类可以跳过。** 比如纯库项目可能没有 workflows，新项目可能没有 pitfalls。空分类不创建目录。

## Phase 3：生成

### 3.1 创建目录结构

```bash
mkdir -p <ROOT>/docs/knowledge/{domains,shared,decisions,integrations,workflows,pitfalls}
```

只创建有条目的分类目录。

### 3.2 并行生成条目（代码全文件精读）

**两阶段策略：Phase 1 快扫定结构，Phase 3 精读写内容。** 没有"快速版"——首次生成默认就要高质量。

为每个条目（或相关的一组条目）启动 Explore 子代理，子代理必须：

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

代码告诉你"是什么"，会话告诉你"为什么/怎么走过来的"。**pitfalls 和 decisions 这两个分类的真正素材永远不在代码里**——只能从历史会话中抢救。这一步和 3.2 并行运行。

**操作步骤：**

1. 定位 jsonl 目录：`~/.claude/projects/<sanitized-cwd>/`（见 Phase 0 关于会话 jsonl 路径的说明）
2. 列出该目录下所有 `.jsonl` 文件
3. 对每个 jsonl 文件启动一个 Explore 子代理（任务模板见下文）
4. 子代理输出按会话粒度的信号摘要，主流程汇总后与 3.2 的代码精读结果**交叉验证**：
   - 同一根因被代码注释 + 多个会话讨论印证 → 高置信度，必入条目
   - 仅会话讨论但代码已不存在 → 不进 domains/shared/integrations/workflows/pitfalls；**可以进 decisions** 作为决策演变记录（"曾经选过 A 后来换成 B"）
   - 仅代码有但无会话讨论 → 按代码事实写入

**子代理任务模板：**

```
完整阅读这个会话 jsonl 的内容,判断该会话是否涉及 `<ROOT>` 内的文件
(看消息中的 file_path、Bash cwd、文件操作等)。

不涉及 → 直接返回"无关",不进一步处理。

涉及 → 提取以下信号(只提取代码里看不出来的信息):
- pitfalls 候选: 用户报告过的 bug、调试中排除的错误假设、时序/并发/平台坑
- decisions 候选: "为什么选 A 不选 B"的讨论、被否决的方案
- workflows 候选: 跨文件/跨模块的操作步骤、易漏点

【隐私边界】写入条目时只输出语义结论(根因、决策、陷阱),
**不要照抄原始用户消息片段、密钥、含用户名的绝对路径**。
条目会被 git 追踪。

按会话内的逻辑顺序读,不要跳跃。
```

**边界情况：**
- jsonl 目录不存在或为空（项目从未在本机用 Claude Code 跑过）→ 跳过这一步,只做 3.2,不报错
- 会话数量极多（项目用了一年累计几百个会话）→ 全部处理,接受这个成本
- 会话讨论的是已被重构掉的旧代码 → 按上面"仅会话讨论但代码已不存在"的规则处理

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

### 3.6 主动识别 pitfalls

pitfalls 在大项目下容易偏保守(信号类型不全)。下面**扩展扫描信号清单 + 加交叉验证机制**——确保会话里讨论过的真实 bug 不会从 pitfalls 里漏掉。

#### 3.6.1 扫描信号清单(在 3.2 子代理任务中并发扫描)

**注释类**:
- `// HACK`、`// FIXME`、`// WORKAROUND`、`// XXX` — 显式标记
- `// NOTE: ...` 后跟超过 30 字的描述 — 通常是非显而易见的约束
- `// TODO:` **如果说的是"防御代码/兼容性"**(例如"// TODO: remove once X is fixed")— 是真坑

**类型与编译绕行**:
- `as any` / `as unknown as X` / `// @ts-ignore` / `// @ts-expect-error` / `// eslint-disable-line` — 类型系统绕行处往往有故事
- Rust 的 `unsafe {}` 块、`unwrap_or_default` 在不该用的地方
- `# type: ignore` (Python) / `nolint` (Go)

**异常处理特殊逻辑**:
- retry 循环、exponential backoff
- catch 后的 fallback / 降级 / 静默 swallow
- 特定 error code 的特殊处理(NS error code、HTTP status 特判)
- `defer` / `finally` 块里的清理代码(顺序敏感)

**时序与并发**:
- `setTimeout(..., 0)`、`requestAnimationFrame` 的双重嵌套(常见为修 layout 时机)
- 不寻常的 `sleep` / `delay` 数字(`sleep(50)` 这种特定值往往有故事)
- `Promise.race` / `AbortController` 的边界处理
- 锁、信号量、原子操作

**平台与版本兼容**:
- `#ifdef` / `#cfg!()` / `process.platform` / `navigator.userAgent` 判断
- 浏览器特定 hack(Safari/iOS/IE)
- 多端编译指令(`#ifdef MP-WEIXIN` 等 uni-app 特有)

**数值与边界**:
- 浮点比较容差(`abs(a - b) < 1e-9`)
- 魔法常数(`8192`、`60000`、`0xFF` 这种)
- 数组/字符串边界处理(off-by-one 修复痕迹)

**序列化与反序列化**:
- `serde(rename = "...")` / `@SerializedName` 修正字段名
- TypeScript union 与后端 enum 的对齐(tagged union)
- JSON 字段大小写转换(camelCase ↔ snake_case)
- Pinia/Vuex 持久化对 Set/Map 的特殊处理

**资源管理**:
- localStorage/IndexedDB 配额检测和降级
- 内存释放、对象池、循环引用拆解
- 事件监听器的 add/remove 配对

**框架特性陷阱**:
- Vue 响应式对 `Map`/`Set`/`Date`/`Object.assign` 的特殊行为
- React `useEffect` 依赖项遗漏的注释痕迹
- Tauri/Electron 的进程间通信限制(payload 大小、序列化限制)

#### 3.6.2 交叉验证

**Phase 3.3 的会话扫描结束后,做交叉验证**,确保会话里讨论过的 bug 不会被漏到 pitfalls:

```
信号源汇总:
  · 代码扫描 pitfalls 候选(3.6.1 各类信号) → 集合 A
  · 会话扫描 pitfalls 候选(Phase 3.3 子代理输出) → 集合 B

交叉验证规则:
  · 在 A 也在 B(代码有 hack 注释 + 会话讨论过) → 高置信度,必入 pitfalls
  · 仅在 A(代码有 hack 但会话没提) → 写入 pitfalls,但 note 标"代码痕迹推断"
  · 仅在 B(会话讨论但代码看不出) → 写入 pitfalls,但必须验证"代码确实是这么修的",不准凭印象
  · 在 B 但代码已不存在(被重构掉) → 走 decisions/ 演变记录,不进 pitfalls

零产出报警:
  会话里出现过 bug 修复 / 调试讨论 / 排除假设 → 但 pitfalls 候选数 = 0 → **异常,重新扫一遍 3.6.1 信号 + 重新读会话**
  这种"空手而归"在大型项目几乎一定是漏了
```

#### 3.6.3 跳过该分类的合理条件

只有**同时满足**下面两条才能跳过 `pitfalls/`:
1. 3.6.1 各类信号在代码里**全都没命中**(不是"主要的没命中",是"全都没命中")
2. 会话扫描产出了**零 pitfalls 候选**

否则**必须产出至少一个 pitfalls 条目**。原版那种"没找到就跳过"在大项目实测中证明不靠谱——LLM 倾向于"觉得没什么大坑"而过度跳过。

#### 3.6.4 pitfalls 条目结构(尽量包含 What/Why/Action)

每个 pitfalls 条目尽量包含:
- **What**: 现象/触发条件(代码里能看到的部分)
- **Why**: 根因(代码里看不出的部分,这是核心)
- **Action**: 怎么避免/修复(具体的代码姿势)

如果某条目暂时缺一两项,可以先写出来,标注"待补",后续追问用户补齐。**不要因为信息不全就跳过**——有半个总比没有强。

#### 3.6.5 独立成条原则 ⭐(防打包合并)

**每个独立陷阱写成一个独立的 md 文件,不要把多个无关陷阱合并到同一文件**。

✅ 正确:
- `pitfalls/streaming-text-block-merging.md`(单一陷阱:流式 text 块合并)
- `pitfalls/serde-frontend-alignment.md`(单一陷阱:Rust enum 与 TS union 字段对齐)
- `pitfalls/decode-path-greedy.md`(单一陷阱:路径解码贪心匹配)

❌ 错误(打包合并,失去快速扫描能力):
- `pitfalls/rust-pitfalls.md`(把 8 个不相关的 Rust 陷阱压成一个文件)
- `pitfalls/vue-pitfalls.md`(把 9 个不相关的 Vue 陷阱压成一个文件)

**判定标准**:如果条目里出现了 ≥2 个**独立的根因或独立的修复姿势**,就该拆。同一根因的不同表现可以合并(比如"X 在场景 A 和场景 B 都会崩"是同一陷阱),不同根因的陷阱必须分开。

**这条规则比"信息量完整"更重要**——读者需要的是"快速定位 + 快速修复",不是"长篇综述"。打包条目违反知识库的核心使用模式。

### 3.7 主动识别 decisions

实测发现 decisions 在两个项目中都偏少(原版 cc-space-tauri 5 个、snap-ub 8 个)——LLM 倾向只把"显眼的选型"(用 Tauri 不用 Electron)写成 decision,漏掉**代码里隐式记录的权衡**。本节扩展信号清单。

#### 3.7.1 扫描信号清单(在 3.2 子代理任务中并发扫描)

**注释类**:
- 代码注释里的 "chose X over Y because..."、"原本用 X,改成 Y"、"为什么不用 X"
- "Why ..." 块注释(常见于 Rust/Go 项目)
- 文件头的"## Design Notes"、"## Trade-offs"

**git 痕迹类**(grep 当前代码即可,不必读 git log):
- 文件名 `*-old.ts`、`*-deprecated.ts`、`Old*Adapter`(说明换过实现)
- import 注释掉的 `// import { X } from 'lib-old'`(候选库被否决的痕迹)
- package.json/Cargo.toml 中 deprecated 依赖的标记
- README/CHANGELOG 里的 "Migrated from X to Y" 段

**自研 vs 引入的权衡**:
- 项目内有自实现工具 + 同一目录有相关第三方库依赖(比如自实现 debounce 但又装了 lodash)
- 项目内有自实现 UI 组件,但 package.json 装了 wot-design-uni / element-plus(说明选择"自研 vs 第三方"的混用策略)

**性能/架构权衡**:
- 显式的"两级缓存"、"懒加载"、"sharding"等模式
- 异常的并行/串行选择(rayon 用在哪、为什么不用在别处)
- 数据库/存储的字段命名缩写(JSON 字段从 `userIdentifier` 缩成 `uid`,空间换性能)

**配置/常量类**:
- 多套配置文件并存(production/staging/dev 用不同策略)
- 魔法常数旁边的解释("180MB 阈值 = 实测 iPhone SE 内存上限")

#### 3.7.2 与会话扫描交叉验证

跟 pitfalls 一样,Phase 3.3 会话扫描产出的 decisions 候选要跟代码扫描的交叉:

```
信号源汇总:
  · 代码扫描 decisions 候选(3.7.1) → 集合 A
  · 会话扫描 decisions 候选(Phase 3.3) → 集合 B

合并规则:
  · 在 A 也在 B(代码痕迹 + 会话讨论过) → 高置信度,必入 decisions
  · 仅在 A(代码有但会话没提) → 写入 decisions,需补充"为什么"段(读代码意图推断)
  · 仅在 B(会话讨论过但代码已不存在) → 走"决策演变记录":"曾经选过 X,后来换成 Y"
```

#### 3.7.3 decisions 条目结构(尽量包含)

- **背景**:什么问题触发了这个决策
- **方案对比**:有哪些候选(A 是什么、B 是什么)
- **最终选择**:选了什么、关键原因
- **副作用**:这个选择带来了什么后续要注意的(可选)

如果某条目暂时缺方案对比(只知道"选了 X"但不知道"否决了 Y"),也可以先写,标注"待补"——不要因为信息不全就跳过。

## Phase 4：索引

生成 `<ROOT>/docs/knowledge/INDEX.md`。**末尾必须写入元信息区**（`codewise_version`、`baseline_commit`、`synced_at`、`scope_root`、`multi_codetree`），增量更新依赖此信息计算 git 差量与会话边界,`multi_codetree` 记录本次覆盖的代码树范围。若不是 git 仓库，`baseline_commit` 写 `null`。

`synced_at` **必须用完整 ISO 8601 时间戳**（精确到秒，含时区），不要只写日期——Phase U 的会话边界判断依赖这个精度。

```markdown
# [项目名] 知识库

快速理解项目的入口。按需跳转，不需要全部阅读。

**技术栈**：[从 Phase 1 探测结果中提取，如 "Vue 3 + TypeScript + uniCloud" 或 "Swift 6 + SwiftUI"]

<!-- codewise-docs:start -->
## 项目文档导航 ⭐

本项目作者维护的人写文档(README / CLAUDE.md / docs/* / 设计文档),**这些是权威源**——比 knowledge/ 自动生成的更准。AI 一进项目要先知道它们存在。

**简介在 Phase 1.1 完整读后写**(50-100 字),不是看 H1 拍脑袋。

[只列项目实际存在的文档,按内容性质分组(入门 / 架构 / 运维 / 业务 / 实施记录 / 开发日志)。文档少时不必分组,直接列表。]

### 入门 / 项目说明
- [README.md](../../README.md) — [50-100 字简介,基于完整阅读]
- [CLAUDE.md](../../CLAUDE.md) — [简介]

### 架构 / 代码地图(如有)
- [docs/CODEBASE_MAP.md](../CODEBASE_MAP.md) — [简介]

### 运维 / 部署(如有)
- [docs/server-setup.md](../server-setup.md) — [简介,标注"操作前必读"等关键提示]

### 业务文档(如有)
- [docs/<name>.md](../...) — [简介]

### 实施记录 / 开发日志(如有)
- [docs/<name>.md](../...) — [简介]
<!-- codewise-docs:end -->

<!-- codewise-interfaces:start -->
## 接口契约速查

本项目所有"对外调用入口"快速索引,**让 AI 一进项目就掌握接口全局地图**(尤其云函数项目接口零散难找)。

**完整签名以代码为准**——本表只列"名 + 职责 + 入口位置",可能滞后一两次 update。

[只列项目实际存在的接口类型,不存在的类型不要出现。每个类型的样板见下方:]

### Tauri Commands(N 个,定义在 src-tauri/src/...)

| 命令 | 职责 | 调用方 |
|---|---|---|
| `get_projects` | 获取项目列表 | `useProjects.loadProjects` |
| ... | ... | ... |

**详情**:[shared/tauri-bridge](shared/tauri-bridge.md) | **完整签名**:src-tauri/src/commands.rs

### Tauri Events(N 个)

| 事件 | 触发时机 | 监听方 |
|---|---|---|
| `projects-changed` | 文件监控防抖 1s 后 | `useProjects.listen` |

### 云函数(N 个,见 cloudfunctions/ 或 uniCloud-*/cloudfunctions/)

按类别分组(如有):
- **autoUpdate***(M 个):`autoUpdateAlbums` | `autoUpdateBundles` | ...
- **业务**(M 个):...
- **官方/工具**(M 个):...

**详情**:[integrations/unicloud-alipay](integrations/unicloud-alipay.md)

### REST endpoints(N 个,如有)

| 方法 | 路径 | 实现位置 |
|---|---|---|
| GET | /api/users | `src/controllers/user.ts:42` |

### 契约文件(GraphQL/gRPC/OpenAPI/.d.ts)

| 文件 | 类型 | 说明 |
|---|---|---|
| `api/openapi.yaml` | OpenAPI | REST 接口完整定义 |
| `proto/service.proto` | gRPC | 跨语言服务契约 |

### 数据库 Schema(如有)

- `uniCloud-alipay/database/users.schema.json` — 用户表 schema 与权限规则
- ...
<!-- codewise-interfaces:end -->

---

## 按功能域

| 条目 | 一句话 |
|------|--------|
| [条目名](domains/xxx.md) | 描述 |

## 按技术层

| 条目 | 一句话 |
|------|--------|
| [条目名](shared/xxx.md) | 描述 |

## 设计决策

| 条目 | 一句话 |
|------|--------|

## 外部集成

| 条目 | 一句话 |
|------|--------|

## 工作流

| 条目 | 一句话 |
|------|--------|

## 踩坑记录

| 条目 | 一句话 |
|------|--------|

---

## 数据流全景

[用文本或 ASCII 画出核心数据流，展示模块间的调用关系]

---

<!-- codewise-meta:start -->
## 同步元信息

- **codewise_version**: `1`
- **baseline_commit**: `<git rev-parse HEAD 写入；非 git 仓库写 null>`
- **synced_at**: `<完整 ISO 8601 时间戳，如 2026-04-30T14:23:01+08:00>`
- **scope_root**: `<ROOT 相对仓库根的路径，根则为 .>`
- **multi_codetree**: `<Phase 1.0 检测出的有效代码树清单,如 "src/, src-tauri/, uniCloud-alipay/";单代码树写 "src/" 即可>`

> 此区域由 codewise 自动维护，**请勿手动编辑**。增量更新基于 `baseline_commit` 计算 git 差量、基于 `synced_at` 判定会话提取边界。`multi_codetree` 字段记录本次扫描覆盖的代码树范围,便于追溯。
<!-- codewise-meta:end -->
```

**空分类不出现在 INDEX.md 中。**

## Phase 5：验证

1. 检查所有互链是否有效（目标文件存在）
2. 检查 INDEX.md 中的链接是否完整
3. 向用户报告：生成了多少条目、多少互链、是否有断链

## Phase 6：注册到 CLAUDE.md

在 `<ROOT>/CLAUDE.md` 中写入硬约束起手式,**让 AI 不能"隐性跳过"读 INDEX**。

**软提示("需要理解项目时,先读 INDEX...")实测无效**——AI 经常自我蒙骗"我已经理解了不需要读"。改为**硬约束 + 后果警告**才有效。

### 写入策略(三种情形)

#### 情形 1:`<ROOT>/CLAUDE.md` 不存在

创建文件,写入下面"标准模板"。

#### 情形 2:`<ROOT>/CLAUDE.md` 存在,但**没有 codewise 标签**

检查 CLAUDE.md 里是否已经有用户手写的"知识库"段(标题含"知识库"/"knowledge")。

- **没有相关段** → 在文件末尾追加"标准模板"
- **有用户手写的相关段** → **停下问用户**:
  > "检测到 CLAUDE.md 已有自定义知识库段。是否升级为 codewise 硬约束模板?
  > Y → 替换为标准模板(用户手写内容会丢失,可先 git commit 备份)
  > N → 保留你的版本不动(下次 update 也不动)"

#### 情形 3:`<ROOT>/CLAUDE.md` 存在,**已有 codewise 标签**

只替换 `<!-- codewise-claude-registry:start --> ... <!-- codewise-claude-registry:end -->` 之间的内容,**标签外用户写的任何内容都不动**。这是 update 时的标准路径。

### 标准模板

```markdown
<!-- codewise-claude-registry:start -->
## 📚 知识库

知识库由 **codewise** skill 生成,入口 [`docs/knowledge/INDEX.md`](docs/knowledge/INDEX.md)。

**任务起手式(硬约束)**:每个新任务第一步 Read INDEX.md(本会话已读过则跳过)。**不读 = 默认从零摸索 = 重复踩前人已经记录过的坑**。

维护:`/codewise update` 增量更新 | `/codewise rebuild` 强制重建 | `/codewise refresh-docs` 局部刷文档导航 | `/codewise refresh-interfaces` 局部刷接口速查。**禁止手编 `docs/knowledge/`**——它是 codewise 单源生成的领地。
<!-- codewise-claude-registry:end -->
```

### 关键设计

- **HTML 标签界定**:跟 INDEX 的 `codewise-{docs,interfaces,meta}:start/end` 同套路,update 时机械替换,不污染用户在标签外的内容
- **不要写温和措辞**(❌ "需要理解时先读 / 推荐先读") → 改为硬指令("第一步 Read")+ 后果警告("不读 = 重复踩坑")
- **不抄 INDEX 内容到 CLAUDE.md**:触发词映射 / 反例 / 条目清单都在 INDEX 里,CLAUDE.md 只负责"让 AI 真去 Read INDEX"
- **仓库根 CLAUDE.md(若 `<ROOT>` 非根)不动** — 只改 `<ROOT>/CLAUDE.md`

**这一步是必须的。** 没有这个硬约束,AI 不会主动查阅知识库——历次实测 codewise 软提示版本 AI 跳过率 >50%。

---

## Phase U：增量更新

当知识库已存在，按以下顺序执行。

**前置**：Phase 0 已读取 INDEX.md 元信息区，拿到 `baseline_commit`。git 仓库 + baseline 存在 → 走 git 差量主路径；否则退化兜底（询问用户 / mtime / 仅会话回顾）。

### U.1 git 时间线采集（主流程，轻量）

仅在 git 仓库且 baseline 存在时执行。**主流程只读元信息和清单，不读完整 diff**——完整 diff 留给 U.5 子代理。

```bash
# 演变路径：所有 commit 的 messages
git log <baseline_commit>..HEAD --oneline -- <ROOT>

# 热点文件：变更行数统计
git diff <baseline_commit>..HEAD --stat -- <ROOT>

# 变更文件清单：U.5 分组依据
git diff <baseline_commit>..HEAD --name-only -- <ROOT>
```

主流程产出两份摘要供后续步骤复用：
- **演变摘要**：commit messages 串起来，理解这段时间项目在做什么
- **变更文件分组**：按功能域 / 顶层目录把变更文件分组，每组将分配一个子代理

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

**偏向记录而非遗漏。** 拿不准时默认写入，宁可条目稍多也不要漏掉根因。空手而归（会话里明明修了 bug 却没产出任何 pitfalls 条目）应视为异常，回头再扫一遍会话历史。

**pitfalls 条目的最低信息量：** 现象、根因（代码看不出的那部分）、修复/规避。三者缺一则继续向用户追问补齐，不要用"修了 XX 文件"这种描述代替根因。

**只提取代码里看不出来的信息。** 代码改了什么、文件结构怎么变的，这些 git diff 已经覆盖——但 git diff **看不到**的根因分析、误判过程、为什么这么改，必须从会话里抢救出来。

**【隐私边界】** 写入条目时只输出语义结论(根因、决策、陷阱),**不要照抄原始用户消息片段、密钥、含用户名的绝对路径**。条目会被 git 追踪。

### U.2.5 跨会话扫描(在 baseline 之后有活动的其他会话)

只读当前会话不够——同一个项目的根因和决策可能散落在多个并行/历史会话里。**必须扫描项目相关的其他会话**,不然每次 update 都只能看到触发 update 那个会话的视角。

**操作步骤:**

1. 定位 jsonl 目录:`~/.claude/projects/<sanitized-cwd>/`(见 Phase 0 关于会话 jsonl 路径的说明)
2. 列出该目录下所有 `.jsonl` 文件
3. **粗粒度筛选**(按文件 mtime,只是性能优化):
   - 文件 mtime ≤ INDEX.md `synced_at` → 整个文件都是旧消息,**跳过**
   - 文件 mtime > `synced_at` 且不是当前会话本身 → 进入下一步
4. 通过筛选的每个 jsonl 启动一个 Explore 子代理(任务模板见下文)
5. 子代理输出按会话粒度的信号摘要,主流程汇总,进入 U.3 与 git 时间线 + 当前会话提取一起做交叉验证

**为什么只用 mtime 粗筛、不做消息级 timestamp 过滤?** 因为按消息时间戳硬切会破坏会话内的语义连续性(后文常引用前文的"那个 bug"、"刚才那个方案"),子代理拿到的会是半截对话,提取质量差。让子代理整段读、用 baseline 当提取边界更可靠。

**子代理任务模板:**

```
完整阅读这个会话 jsonl(从头到尾,不要跳跃)。

第一步: 判断该会话是否涉及 `<ROOT>` 内的文件
(看消息中的 file_path、Bash cwd、文件操作等)。
  不涉及 → 直接返回"无关",不进一步处理。
  涉及 → 进入第二步。

第二步: baseline 是 `synced_at` = <T1>。
  完整理解会话上下文(包括 T1 之前的部分,作为背景)
  但**只为 timestamp > T1 的讨论产出信号**:
  - pitfalls 候选: bug 报告、调试中排除的错误假设、时序/并发/平台坑
  - decisions 候选: "为什么选 A 不选 B"的讨论、被否决的方案
  - workflows 候选: 跨文件/跨模块的操作步骤、易漏点
  T1 之前的部分已被上次 update 处理,不重复产出。

【隐私边界】写入信号摘要时只输出语义结论(根因、决策、陷阱),
**不要照抄原始用户消息片段、密钥、含用户名的绝对路径**。
条目会被 git 追踪。

按会话内的逻辑顺序读,不要按全局 timestamp 重排。
```

**边界情况:**
- jsonl 目录不存在或为空 → 跳过 U.2.5,只用 U.2 + U.1 + U.3 走完流程
- 会话讨论的代码已被重构掉 → 同 Phase 3.3 规则:不进 domains/shared/integrations/workflows/pitfalls,但可进 decisions 作演变记录

### U.3 交叉验证 git ↔ 会话

把 U.1 的演变摘要和 U.2 + U.2.5 的会话提取放在一起对比，捕捉以下信号：

- **会话讨论过 X 修复，但 git 里没动 X** → 可能是讨论但没做、在另一分支、或被回滚；追问用户
- **git 大改了 Y，会话里没提** → Y 可能不是本次记录的产物（其他人/其他会话/未本机用过 Claude Code 的提交），不需要从会话补 pitfalls，但条目内容要据 diff 更新
- **commit message 透露的意图与会话根因不一致** → **以会话为准**，commit message 经常省略真实原因
- **同一根因被多个会话(U.2 + U.2.5 中的不同 jsonl)印证** → 高置信度,值得入条目;只在一处出现的可下调置信度

矛盾不必全部当场解决，但要带入 U.5——它们影响子代理的分发策略和条目内容侧重。

### U.4 影响评估

基于 U.1 变更文件清单 + U.2/U.2.5 会话提取 + U.3 交叉验证，读取 `<ROOT>/docs/knowledge/INDEX.md` 和相关条目，判断：
- 哪些现有条目需要更新？（变更文件命中条目的"关键文件"列表）
- 是否需要新增条目？（出现新功能域、新依赖、新 workflow）
- 是否有条目应该删除？（对应模块整体被移除）

#### U.4.1 文档变更专项规则 ⭐

变更文件清单里的 `.md` 文件单独识别处理(不走 U.5 子代理流程):

```
变更文件清单中提取所有 .md 路径,排除 docs/knowledge/** 内的:
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

**核心原则：主流程不读完整 diff。** 按 U.1 的"变更文件分组"为每组启动一个 Explore 子代理，子代理读自己负责文件的完整 diff，主流程只汇总。这样每一行 diff 都被读到，且不会爆主 context。

每个子代理的任务模板：

```
本组负责的变更文件：
[文件清单]

任务：
1. 读取这些文件当前的完整内容（`<ROOT>` 内的实际代码）
2. 读取这些文件从 baseline 到 HEAD 的完整 diff：
   git diff <baseline_commit>..HEAD -- [文件清单]
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
4. 确认 `<ROOT>/CLAUDE.md` 中的知识库指引仍然存在
5. **最后**才更新 INDEX.md 元信息区:`baseline_commit` 改为当前 `git rev-parse HEAD`、`synced_at` 改为当前完整 ISO 8601 时间戳

**为什么要这个顺序?** 如果中途崩了,baseline 没更新,下次 update 仍然从旧 baseline 算 diff——会重复处理这次没改完的部分,但不会丢东西。**重跑是安全的**。

向用户报告(末尾输出):

```
✓ 更新完成
  · 条目变更: 更新 N、新增 M、删除 K
  · 旧 baseline: <8 位短码>(synced_at: <旧时间>)
  · 新 baseline: <8 位短码>(synced_at: <新时间>)
  · 下次 update 将从 <新 baseline 短码> 开始计算 diff
```

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

**前置条件**:`<ROOT>/docs/knowledge/INDEX.md` 已存在(否则走首次生成,不是 refresh)。

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

## 条目模板

### domains/（功能域）

```markdown
# [功能名称]

[一句话描述这个功能域做什么]

## 架构

[文本/ASCII 架构图，展示核心组件和数据流]

## 关键文件

| 文件 | 职责 |
|------|------|
| `路径/文件名` | 一句话 |

## 核心流程

[描述主要的数据流或执行流程]

## 关联条目

- [条目名](../分类/文件名.md) — 关联原因
```

### shared/（公共模块）

```markdown
# [模块名称]

[一句话描述]

## 核心类型/接口

[列出关键的类型定义、函数签名、使用方式]

## 使用方

[谁在用这个模块，怎么用]

## 关联条目

- [条目名](../分类/文件名.md) — 关联原因
```

### decisions/（设计决策）

```markdown
# [决策标题]

[一句话描述这个决策]

## 背景

[什么问题触发了这个决策？]

## 方案对比

| | 方案 A | 方案 B |
|---|---|---|
| 优势 | ... | ... |
| 劣势 | ... | ... |

## 最终选择

[选了什么，为什么]

## 关联条目

- [条目名](../分类/文件名.md) — 关联原因
```

### integrations/（外部集成）

```markdown
# [依赖/服务名称]

[一句话描述为什么用它]

## 选型原因

[为什么选这个而不是替代品]

## 使用方式

[怎么集成的，关键配置]

## 限制与注意

[已知限制、版本要求、许可证等]

## 关联条目

- [条目名](../分类/文件名.md) — 关联原因
```

### workflows/（工作流）

```markdown
# [流程名称]

[一句话描述这个流程的目的]

## 步骤

1. [步骤描述]（涉及文件：`路径`）
2. ...

## 注意事项

[容易遗漏的点]

## 关联条目

- [条目名](../分类/文件名.md) — 关联原因
```

### pitfalls/（踩坑记录）

```markdown
# [陷阱主题]

## [具体陷阱名称]

**What**: [发生了什么]
**Why**: [为什么会这样]
**Action**: [怎么避免/解决]

[按主题分组，每个陷阱用三段式]

## 关联条目

- [条目名](../分类/文件名.md) — 关联原因
```

---

## 质量标准

- **条目独立可读**：不依赖其他条目也能理解
- **互链有意义**：只链接真正相关的条目
- **粒度适中**：一个条目 30-80 行，超过 100 行就拆分
- **不重复代码注释**：知识库记录的是代码里看不出来的信息
- **保持更新**：过时的知识库比没有更糟
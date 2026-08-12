# INDEX.md 模板

Phase 4 生成 `<KBR>/INDEX.md` 的完整模板，含各分区结构与末尾的同步元信息区。
空分类不出现在 INDEX.md 中。

## 引用写法（两类，规则不同）

| 引用类型 | 写法 | 为什么 |
|---|---|---|
| **KB 内部互链**（条目 ↔ 条目） | markdown 链接 `[名](../pitfalls/x.md)` | 同一仓库内，相对路径永远有效，clone 走也不断 |
| **指向工作树**（项目文档、源码） | 反引号路径 `` `CLAUDE.md` ``，**不用链接语法** | KB 在 `git-common-dir` 下，指向工作树要退好几层；一旦 KB 被 clone 到别处，这类链接全断 |

知识库的主要读者是 AI——它读到路径会用文件工具去读，可点击链接是给人的次要便利，不值得换来「clone 后全断」。

「项目文档导航」区顶部必须写一行锚点说明：

```markdown
> 以下路径相对项目根（`git rev-parse --show-toplevel`）
```


```markdown
# [项目名] 知识库

快速理解项目的入口。按需跳转，不需要全部阅读。

**技术栈**：[从 Phase 1 探测结果中提取，如 "Vue 3 + TypeScript + uniCloud" 或 "Swift 6 + SwiftUI"]

<!-- codewise-docs:start -->
## 项目文档导航 ⭐

本项目作者维护的人写文档(README / AGENTS.md / CLAUDE.md / docs/* / 设计文档),**这些是权威源**——比 knowledge/ 自动生成的更准。AI 一进项目要先知道它们存在。

**简介在 Phase 1.1 完整读后写**(50-100 字),不是看 H1 拍脑袋。

[只列项目实际存在的文档,按内容性质分组(入门 / 架构 / 运维 / 业务 / 实施记录 / 开发日志)。文档少时不必分组,直接列表。]

### 入门 / 项目说明
- [README.md](../../README.md) — [50-100 字简介,基于完整阅读]
- [AGENTS.md](../../AGENTS.md) — [简介]
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

- **codewise_version**: `3`
- **baseline_commit**: `<git rev-parse HEAD 写入；非 git 仓库写 null>`
- **anchor_kind**: `<main | branch:<原始分支名>；标明本目录是主线还是某分支的稀疏知识库>`
- **synced_at**: `<完整 ISO 8601 时间戳，如 2026-04-30T14:23:01+08:00>`
- **scope_root**: `<ROOT 相对仓库根的路径，根则为 .>`
- **multi_codetree**: `<Phase 1.0 检测出的有效代码树清单,如 "src/, src-tauri/, uniCloud-alipay/";单代码树写 "src/" 即可>`
- **session_sources**: `<最近一次扫描实际发现的来源与数量，如 "codex:12, claude:8, gemini:2"；未解析来源写 "cursor:detected-unparsed">`
- **worktree_count**: `<最近一次扫描覆盖的同项目 Worktree 数量>`
- **known_worktrees**: `<发现脚本输出的 project.known_worktrees 原样写入，逗号分隔；均为相对仓库根的路径>`

> 此区域由 codewise 自动维护，**请勿手动编辑**。增量更新基于 `baseline_commit` 计算 git 差量、基于 `synced_at` 判定会话提取边界；`anchor_kind` 标明本目录的归属，`multi_codetree`、`session_sources` 与 `worktree_count` 用于披露最近一次覆盖范围。`known_worktrees` 是**必须持久化**的累积集合：Worktree 一旦被 `git worktree remove`/`prune` 移出注册表，其下所有会话就再也无法通过路径发现，而短期实验 Worktree 用完即弃——丢失是静默且不可补救的。这里只写相对路径，不存储本机绝对路径。
<!-- codewise-meta:end -->
```

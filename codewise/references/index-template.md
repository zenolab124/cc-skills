# INDEX.md 模板

Phase 4 生成 `<KBR>/INDEX.md` 的完整模板。**同步状态写 `<KBR>/_sync.json`，不写进 INDEX.md**——见 [storage-layout.md](storage-layout.md)。
空分类不出现在 INDEX.md 中。

## 目录

1. 引用写法
2. INDEX 主模板
3. Agent 注册模板

## 引用写法（两类，规则不同）

| 引用类型 | 写法 | 为什么 |
|---|---|---|
| **KB 内部互链**（条目 ↔ 条目） | markdown 链接 `[名](../pitfalls/x.md)` | 同一仓库内，相对路径永远有效，clone 走也不断 |
| **指向工作树**（项目文档、源码） | 反引号路径 `` `CLAUDE.md` ``，**不用链接语法** | 知识库是独立仓库，可以被 clone 到别处查看——那时指向工作树的链接会**静默失效**。本地能点不值得换来这个 |

知识库的主要读者是 AI——它读到路径会用文件工具去读，可点击链接是给人的次要便利，不值得换来「clone 后全断」。

「项目文档导航」区顶部必须写一行锚点说明：

```markdown
> 以下路径相对本次 Codewise scope 根（见 `_sync.json.scope_root`）
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
- **`README.md`** — [50-100 字简介,基于完整阅读]
- **`AGENTS.md`** — [简介]
- **`CLAUDE.md`** — [简介]

### 架构 / 代码地图(如有)
- **`docs/CODEBASE_MAP.md`** — [简介]

### 运维 / 部署(如有)
- **`docs/server-setup.md`** — [简介,标注"操作前必读"等关键提示]

### 业务文档(如有)
- **`docs/<name>.md`** — [简介]

### 实施记录 / 开发日志(如有)
- **`docs/<name>.md`** — [简介]
<!-- codewise-docs:end -->

<!-- codewise-interfaces:start -->
## 接口契约速查

本项目所有"对外调用入口"快速索引,**让 AI 一进项目就掌握接口全局地图**(尤其云函数项目接口零散难找)。

**完整签名以代码为准**——本表只列"名 + 职责 + 入口位置",可能滞后一两次 update。

[只列项目实际存在的接口类型,不存在的类型不要出现。每个类型的样板见下方:]

### Tauri Commands(N 个,定义在 `src-tauri/src/...`)

| 命令 | 职责 | 调用方 |
|---|---|---|
| `get_projects` | 获取项目列表 | `useProjects.loadProjects` |
| ... | ... | ... |

**详情**:[shared/tauri-bridge](shared/tauri-bridge.md) | **完整签名**:`src-tauri/src/commands.rs`

### Tauri Events(N 个)

| 事件 | 触发时机 | 监听方 |
|---|---|---|
| `projects-changed` | 文件监控防抖 1s 后 | `useProjects.listen` |

### 云函数(N 个,见 `cloudfunctions/` 或 `uniCloud-*/cloudfunctions/`)

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

> 同步状态（baseline / synced_at / known_worktrees 等）不在本文件，见 `_sync.json`。
> 那些字段每次运行都变，放在 INDEX 里会让两台机器的 merge 必然冲突。
```

## Agent 注册模板

Phase 6 必须同时维护 scope 根的 `AGENTS.md` 与 `CLAUDE.md`，两者标签块内容一致：

1. 文件不存在则创建；已有 `codewise-registry` 标签只替换标签内内容。
2. 旧 `codewise-claude-registry` 原位迁移；无标签且无知识库段时追加。
3. 已有用户自定义知识库段时停下询问，标签外一字不动。

```markdown
<!-- codewise-registry:start -->
## 📚 知识库

主工作树中的入口：`docs/knowledge/INDEX.md`。它是被主仓库忽略的独立 Git 仓库。

- 每个新任务先读取 INDEX；当前分支有独立目录时读 `.branches/<slug>/INDEX.md`。
- linked worktree 中该相对路径不存在时，以“包含本文件的 scope 目录”为 ROOT，定位主工作树及相同 scope 下的 `docs/knowledge`；不要在 linked worktree 新建一份。
- fallback 到父锚点时明确标注“这是父分支视角，不含本分支未合并改动”。

维护：显式调用 `$codewise`（或当前客户端等价形式）执行 update/rebuild/refresh/merge。不要手工维护目录结构、归属或同步元信息；条目正文允许人工补充，Codewise 更新时必须保留。
<!-- codewise-registry:end -->
```

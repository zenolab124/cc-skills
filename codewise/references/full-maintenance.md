# Codewise 完整知识库维护（显式模式）

生成网状互链的模块化知识库，让人和 AI 从 `INDEX.md` 按需跳转。

本文件仅在用户明确要求完整扫描、重建、旧库刷新、分支合并或身份修复时读取。普通 `update` 不进入本流程。`<SKILL_DIR>` 始终指包含顶层 `SKILL.md` 的技能目录，不是本 references 目录。

## 执行契约

- 主代理负责 scope、事实边界、冲突裁决与最终写入；子代理按代码树、diff 或会话分片精读。运行时无子代理时串行执行，不能跳过任何文件或候选会话。
- 读取用户本次请求中的模式、路径和 scope description；不依赖客户端专属 `$ARGUMENTS`。
- 所有写入必须先完成路径、身份、父仓库追踪、锁和远端 freshness 检查。
- 当前事实只由当前代码与当前 HEAD 历史证明；会话用于补充根因、失败尝试和决策原因。
- 知识库只写语义结论与 `<ROOT>` 相对路径，不复制原始用户消息、thinking、密钥、令牌、邮箱或含用户名的绝对路径。

## 模式与按需引用

先解析模式再解析路径：`merge` 后第一个参数固定是原始分支名，不能因它恰好也是目录而当作 `<ROOT>`。scope 只接受显式 `--root <path>`；未给时默认 `.`。自然语言同时给出多个现存目录且没有 `--root` 时停下确认，不能取“第一个目录”。模式：

| 参数 | 行为 |
|---|---|
| `full-update` | Phase 0 解析后的 `<KBR>` 已有 INDEX 时完整增量更新；根 KB 没有时首次生成 |
| `rebuild` | 确认后完整重建，覆盖自动生成内容 |
| `refresh-docs` | 只刷新 INDEX 的项目文档导航 |
| `refresh-interfaces` | 只刷新 INDEX 的接口契约速查 |
| `merge <branch> [--root <path>]` | 将独立分支知识合入主线知识库 |
| `reidentify` | 用户明确确认合法迁移后重建身份锚点 |

开始任何模式前完整读取：

- [storage-layout.md](storage-layout.md)：统一路径、身份、父仓库安全、锁、bootstrap 与跨机器同步。
- [branch-resolution.md](branch-resolution.md)：`<KBR>`、分支登记、会话分段与事实采纳。

按模式再读取：

- 首次/rebuild：[detection.md](detection.md)、[session-sources.md](session-sources.md)、[signal-extraction.md](signal-extraction.md)、[entry-templates.md](entry-templates.md)、[index-template.md](index-template.md)。
- full-update：`session-sources`、`signal-extraction`；涉及文档/接口时再读 `detection`；写条目/索引时读对应模板。
- refresh：`detection` + `index-template`。
- merge：[merge-protocol.md](merge-protocol.md) + `index-template`。

## Phase 0：统一预检

### 0.1 只解析一次 `<KB>`

运行：

```bash
python3 <SKILL_DIR>/scripts/resolve_context.py <ROOT> --pretty
```

仅 `reidentify` 模式改用 `--reidentify --pretty`。`reidentify` 只把 identity 类失败转为待确认的 `identity_override_reasons`；其他 hard stop 仍然生效。展示旧身份、实测新身份和迁移原因并取得用户确认后，才可重写 `_identity.json`，不能顺带改条目或 baseline。

从输出取得规范化 `<ROOT>`、主工作树、`scope_root` 和唯一 `<KB>`。之后旧格式守卫、身份、锁、remote 与知识库 Git 命令只使用该 `<KB>`，禁止重新拼路径。

脚本返回 hard stop 时停止且零写入：KB 路径包含软链或越出主工作树、linked worktree 缺根知识库、目录不是独立仓库、父仓库仍追踪知识库、ignore 未生效、身份锚点不匹配，或主工作树解析失败。

resolver 只做只读路径/身份初筛；已有 KB 随即先取得内部锁，并在锁内运行：

```bash
python3 <SKILL_DIR>/scripts/kb_tree_guard.py '<KB>' --scope '<ROOT>' --pretty
python3 <SKILL_DIR>/scripts/validate_control_state.py --kb '<KB>' --source-root '<ROOT>' --pretty
```

前者拒绝控制文件、六类目录/条目、分支目录或 scope registry 的软链、特殊文件与越界路径；后者校验 meta/sync/branch registry 的 schema、安全 slug、分支名与完整 commit id，并只输出经 Git 验证的规范 commit；identity 仍由 resolver 校验。fetch/reconcile 后在同一锁内重跑 resolver + 两个 guard，只消费第二轮输出；bootstrap 原子落位后继续持内部锁并同样复验。首次目录要在临时 KB 内先写最小合法 `_meta.json`、空 `_branches.json`、baseline/synced_at 为 null 的 `_sync.json`，通过 guard 后才能生成内容。禁止直接把控制 JSON 字符串拼入路径或 Git 命令。

主工作树首次初始化时按 `storage-layout` 处理 bootstrap。若 scope 根存在已提交、未修改且通过 `resolve_bootstrap_hint.py` 校验的 `.codewise-bootstrap.json`，把它视为项目已明确登记的 existing remote 与主线 anchor，不再重复询问；仍须展示物理主工作树分支、`origin/HEAD`、HEAD 与 hint，并在不一致时停止。没有合法 hint 时才让用户选择已有 remote、明确建新库或取消。非 Git scope 与零 commit 各询问一次并披露能力降级；拒绝 Git 初始化时记录 `degraded_acknowledged`。

有 HEAD 的 Git scope 中，首次、rebuild、full-update、refresh、merge 只要 `source_dirty=true` 就停止，要求先提交或 `git stash` `<ROOT>` 内改动；仅 `git add` 仍是脏状态。不能把未提交文件写进知识后仍用 HEAD 当 baseline。纯 `reidentify` 不读源码，可跳过此项。

所有会写 KB 的模式先运行以下命令，并从精简 JSON 的 `digest` 固定 `SOURCE_SNAPSHOT`；调试时才加 `--list-files`：

```bash
python3 <SKILL_DIR>/scripts/source_snapshot.py '<ROOT>' --kb '<KB>' --pretty
```

有 HEAD 时同时固定 resolver 输出的 `SOURCE_HEAD`；零 commit/非 Git 允许 untracked 源码并只依赖内容快照。任何 KB 写入前、更新 baseline 前都运行：

```bash
python3 <SKILL_DIR>/scripts/source_snapshot.py '<ROOT>' --kb '<KB>' --verify "$SOURCE_SNAPSHOT"
python3 <SKILL_DIR>/scripts/resolve_context.py '<ROOT>' --require-clean-source --verify-source-head "$SOURCE_HEAD"
```

第二条仅用于初始已有 HEAD 的 Git scope，但所有模式在每次 snapshot verify 后都要无条件重跑普通 resolver，并要求 `is_git`、`has_commits`、`source_head` 与初始值一致；snapshot digest 本身也包含这些状态，能阻止运行中 non-Git→Git、unborn→首个 commit 或 HEAD 切换。registry 写入放在最后一次源码复核之后；写完只校验标签块，没有其他项目文件变化。出现变化立即停止，不提交 KB、不前移 baseline，并报告未提交 KB 草稿供恢复。

首次新增主仓库 `.gitignore` 规则后先停下，让用户提交该规则，再从 Phase 0 重跑；不能把它当作源码快照豁免。

### 0.2 先同步，再读 baseline

所有写模式严格执行 `storage-layout` 的唯一锁协议：已有 KB 持内部 token 锁；缺失 KB 先持 bootstrap token 锁，并在临时 KB 内预持内部锁后原子落位。整个流程 finally 用对应 token 释放；不得依赖一次性 helper PID，也不得自行删锁。有 `origin` 再执行 `fetch/reconcile`。只允许 fast-forward 或普通三方合并；内容冲突停下，保留 safety ref、本地 commit 与 mirror；禁止 force。fetch 失败可继续本地生成，但必须标 `remote freshness unknown`。

### 0.3 决定 `<KBR>` 与写权限

按 `branch-resolution` 读取主线 `_meta.json` 和 `_branches.json`：

- 主线：`<KBR>=<KB>`，可写。
- `independent`：写 `.branches/<slug>`，分支 `_sync.json` 独立；首次登记时按 `branch-resolution` 从主线物化完整快照并直接进入 Phase U。
- `inherit`、`archived`、未登记或 detached HEAD：只读；不得把分支事实写进父目录。archived 同名分支复用按 `branch-resolution` 迁移旧记录到 `archive_history` 后重新确认。
- 未登记非主线仅展示拓扑候选并询问一次，确认后登记；不能靠 merge-base 距离自动选择 sibling。

从 `<KBR>/_sync.json` 读取 `baseline_commit`、`synced_at`、`known_worktrees`。报告解析后的 `<ROOT>`、`<KB>`、`<KBR>`、分支归属、写权限、远端 freshness 与本次模式，然后继续。

### 0.4 需要确认或停止的边界

以下情况必须停下来：没有合法 bootstrap hint 的新库 bootstrap、非 Git、零 commit、未登记分支、rebuild、仓库根 monorepo、首次/rebuild 规划条目数 `<5` 或 `>80`、已有自定义知识库 registry、身份或父索引异常、Git/语义合并冲突。合法 hint 只免除重复选择 remote/anchor，不免除 bootstrap 锁、临时 clone、fsck、tree/control guard 与 identity 校验。普通 scope 报告、条目规划和常规 full-update 不等待确认。

## Phase G：首次生成与 rebuild

### G.1 机械探测

严格执行 `detection.md`：检测全部代码树、monorepo、元文件、动态扩展名、排除规则、接口契约和项目文档。先向用户报告结果；每棵代码树都必须覆盖，不能假设只有 `src/`。

完整读取 `<ROOT>` 内每份项目文档后写 50–100 字简介。文档提供意图，代码验证技术事实；冲突时以代码为准并记录差异。已有架构图优先吸收，不凭印象重画。

### G.2 规划条目

严格按 `entry-templates.md` 的 What/How/Why/With-what/How-to/Watch-out 边界规划六类：`domains/`、`shared/`、`decisions/`、`integrations/`、`workflows/`、`pitfalls/`。必须有位于功能域首项的 `architecture-overview`，覆盖全部代码树、数据流、子系统职责与通信桥梁。

一个条目只讲一件事，目标 30–80 行；两个独立根因/流程就拆，拆后不足 20 行则与真正相关条目合并。不要删除真实第三方 integration，不把多个公共组件或独立 pitfall 打包。与上一版相比分类条目显著减少时必须解释或回查。

展示标题与一句话规划后直接继续；只有异常数量或仓库根 monorepo 停下来确认。

### G.3 全源码精读与生成

按 `detection.md` 分组。每组完整读取负责范围的全部源码，回查是否遗漏功能，并扫描生命周期副作用、动态加载、retry/fallback、平台边界和 HACK/FIXME/TODO/WORKAROUND。按 `entry-templates.md` 写条目；双向互链，只链接真实相关项。

按 `signal-extraction.md` 同时识别 pitfalls 与 decisions。一个独立陷阱一个文件；信息暂缺时写 `confidence: medium` / 待补，不因不完整丢弃。代码与会话都出现的根因优先升级置信度。

### G.4 全项目会话扫描

运行统一发现器并回传历史 Worktree：

```bash
python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> \
  --known-worktree '<known_worktrees 每一项>' --pretty
```

若检测到会话目录含跨机器同步副本，再加 `--include-unmatched`；这只扩展 Claude/Codex 候选，所有新增候选都必须做内容级 scope 过滤。

向用户报告 Worktree、来源、候选数和未解析来源。每个候选从头到尾读取，数量大时分批但不抽样、不只读标题/最终回复。先用编辑/补丁路径、tool cwd、diff 等内容证据做 scope 过滤，再按 `session-sources.md` 输出归一化摘要。去重必须按 `session-sources` 的副本合并规则，不能简单丢掉同 id 文件。

跨分支会话按消息/标记点切段。未合并且仍存在的分支事实隔离；分支已删除的失败实验可把因果结论写为 rejected decision/pitfall。状态断言仍须当前代码验证。

### G.5 索引、状态与注册

按 `index-template.md` 生成 `<KBR>/INDEX.md`。工作树文档/源码只写反引号路径，KB 内互链使用 Markdown 相对链接。

Phase 0 bootstrap 已创建最小控制状态；这里只更新、以后只核验 `<KBR>/_meta.json`。Git 主线 `anchor_kind:"main"` 且分支名必须实测；非 Git 根使用 `anchor_kind:"non-git"`、无 branch；独立分支额外记录 `forked_from`。准备 `_sync.json` 的新值，至少含 `codewise_version`、当前 HEAD baseline（非 Git/null）、完整 ISO 8601 `synced_at`、`scope_root`、代码树、会话来源、Worktree 数和发现器原样返回的 `known_worktrees`，但在 G.6 全部验证通过前不得落盘新 baseline。静态 `_identity.json` 只在初始化或已确认的 `reidentify` 中写入，必须含 scope 与强/弱身份锚点。

按 `index-template` 的 registry 规则同时维护 `<ROOT>/AGENTS.md` 与 `<ROOT>/CLAUDE.md`，只改标签块；用户自定义段不擅自覆盖。

### G.6 验证与落盘

验证所有 KB 互链、INDEX 完整性、代码树/Worktree/会话来源覆盖和断链数。最后才更新 baseline。提交 KB，先推本地 mirror，再按 `storage-layout` 普通 push origin；non-fast-forward 最多两轮 fetch/reconcile，绝不 force。

## Phase U：增量更新

### U.1 建立差量

只有 baseline 可解析时走 Git 差量。先判方向：

- baseline 是 HEAD 祖先：比较 `baseline..HEAD`。
- HEAD 是 baseline 祖先：主仓库落后，停止并提示先同步主仓库。
- 发生 rebase/分叉：用 `merge-base`，报告降级。
- 无共同祖先/缺 baseline：报告原因，按完整扫描或 mtime 兜底；不能构造空的 `..HEAD`。

主代理只读完整 commit body、merge commits、stat 和变更文件清单。按功能域分组后让子代理读取当前完整文件及完整 diff；来自其他人/机器且没有会话的修改，从 commit body、PR/issue、CHANGELOG 和代码注释抢救，标 `evidence: code-only`。

对其他活跃 Worktree 分别计算自己的 merge-base、提交差量与未提交状态；stale Worktree 只用于会话匹配。它们是候选，不是当前事实源。

### U.2 当前与其他会话

按 `signal-extraction.md` 完整回顾当前会话，不用 timestamp 截断阅读，只用 `synced_at` 限制新产出。Codex 用发现结果中唯一 `is_current=true` 的 rollout 定位磁盘副本；压缩时按 `latest_compaction_line` 补读边界前内容。

再以 `--since`、`--baseline` 和全部 `known_worktrees` 扫描其他会话。每个候选完整阅读后只为 `timestamp > synced_at` 的信号产出；无消息时间时用 mtime 并标降级。`multi_branch=true` 必须分段。

### U.3 交叉验证与更新

每个信号按 `branch-resolution.md` 定性：

- 状态断言必须由当前代码验证。
- 因果断言按合并状态采纳；失败实验的原因不能因代码消失而丢掉。
- 会话说已修但当前代码没有、git 大改但会话没提、commit 意图与会话根因冲突，都进入待裁决清单。

读取 INDEX 与相关条目，确定更新/新增/删除。`.md` 变更按 `detection.md` 重读全文刷新文档导航；接口文件变更重跑契约抽取。其他组由子代理读完整 diff，先读现有条目再修改，保留人工补充。

写入顺序固定：条目 → INDEX → 互链检查 → registry 检查 → 最后 `_sync.json` baseline/synced_at/来源/Worktree。任一步失败都不前移 baseline。

提交、mirror、push 后报告条目变更、旧/新 baseline、会话覆盖、远端状态；`evidence: code-only` 与 `confidence: medium` 必须单列。发现已合并但未并入的独立分支时只提示 `$codewise merge <branch>`，不自动合并。

## Phase R：局部刷新

要求 `<KBR>/INDEX.md` 已存在且当前归属可写。按 `detection.md` 执行：

- `refresh-docs`：完整读项目文档，只替换 `codewise-docs` 区。
- `refresh-interfaces`：机械重抽接口，只替换 `codewise-interfaces` 区。

不修改其他条目、`baseline_commit` 或 `synced_at`；与其他写模式一样要求 clean/snapshot 不变量。局部刷新不能替代 full-update。

## Phase M：分支知识合并

仅显式 `merge <branch>` 执行。严格按 `merge-protocol.md`：用分支 `_sync.json.baseline_commit` 验证代码已进入当前 HEAD；先以规范 `<KB>` 传 `--kb` 运行 `scripts/branch_manifest.py`，后续只消费其六类 `.md` 白名单与已整体校验的 `_deleted`；控制文件不复制到根。

以分支 `_meta.json.forked_from` 取得 base 做条目级三方合并。状态冲突由当前代码裁决，因果条目取并集；语义重复写 `_dedup_pending.md`，不自动删信息。重建根 INDEX、验证互链后归档分支目录。

Phase M 不前移根 baseline；完成后提示按需运行 full-update。最后 commit、mirror、普通 push。

## 完成标准

- 每棵代码树与每个候选会话均已覆盖或明确报告无法解析原因。
- INDEX、条目互链、项目文档路径与 registry 一致，无断链。
- 主仓库不追踪 KB；身份含 scope；锁未提交；没有 force push、唯一副本或临时文件遗留。
- 报告知识条目/互链/会话/Worktree 数、baseline、远端 freshness、code-only 与待确认项。
- 若本次新建或修改 `<ROOT>/AGENTS.md`、`<ROOT>/CLAUDE.md`，单列这些主仓库文件并要求用户审阅后自行 commit；Codewise 不自动提交主仓库，否则下次会被 source-dirty 守卫阻塞。
- 过时知识比没有更糟；无法证明的状态不写成当前事实。

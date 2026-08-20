# 知识库存储布局

知识库放在 `<ROOT>/docs/knowledge/`，但它**不是主仓库的内容**——主仓库把它 gitignore 掉，它自己是一个独立的 git 仓库。

这样同时拿到三件事：位置直观（人能看见、能搜索，Agent 直接 Read 无需先跑命令）、有自己的版本历史（三方合并的前提）、且完全不参与主仓库的分支与合并。

## 目录

1. 路径解析
2. 目录结构
3. 主仓库侧的两项前置
4. 项目身份校验
5. 并发锁
6. 跨机器 bootstrap 与同步
7. 旧格式守卫
8. 非 git 降级模式

## 路径解析

| 占位符 | 取值 |
|---|---|
| `<KB>` | `<ROOT>/docs/knowledge` —— 独立 git 仓库，被主仓库 gitignore |
| `<KBR>` | 本次实际读写的知识库根，由归属分支决定 |

`<KBR>` 的取值（推导规则见 [branch-resolution.md](branch-resolution.md)）：

- 归属主线 → **`<KB>` 本身**，即 `docs/knowledge/INDEX.md`。这是 99% 的访问路径，刻意做到最短
- 归属某个有独立目录的分支 → `<KB>/.branches/<branch-slug>`

`<branch-slug>` 把分支名里非 `[A-Za-z0-9._-]` 的字符替换为 `-`。slug 可能与另一个分支撞车（`feat/big` 与 `feat-big`），所以每个分支目录的 `_meta.json` 必须记录**原始分支名**；发现 slug 已被另一个分支占用时报错停止，不要覆盖。

**主线也要有 `_meta.json`**（在 `<KB>/_meta.json`）。仓库的主线分支可能叫 `master`、`trunk` 或别的——首次生成时把当时的分支名写进去，之后归属推导从这里读。**不要把字符串 `"main"` 当分支名**，那在 `master` 仓库上会静默失配。

**不需要 scope 分隔**：monorepo 的每个子项目有自己的 `<ROOT>/docs/knowledge/`，天然隔离，各自是独立仓库。

### 统一解析（主工作树与 Worktree 都使用）

先把用户请求解析出的 `<ROOT>` 规范化，再计算 scope；**不能用当前 cwd 代替 `<ROOT>`**。以下结果是本次唯一的 `$KB`，后续旧格式守卫、身份、锁、分支归属、remote 和所有知识库 git 命令都必须消费它，不得再次拼 `<ROOT>/docs/knowledge`。

```bash
ROOT=$(cd "<用户解析出的 ROOT>" && pwd -P)
WT_TOP=$(git -C "$ROOT" rev-parse --show-toplevel)
SCOPE_REL=$(git -C "$ROOT" rev-parse --show-prefix)   # 相对当前 worktree 根，末尾带 /
MAIN=$(git -C "$ROOT" worktree list --porcelain | sed -n '1s/^worktree //p')
MAIN=$(cd "$MAIN" && pwd -P)
KB="${MAIN}/${SCOPE_REL}docs/knowledge"
```

`sed` 去掉固定前缀而不是按空格切字段，主工作树路径含空格时也不会截断。解析后校验 `$ROOT` 与 `$MAIN` 的 `git-common-dir` 相同且 `$MAIN` 存在；失败则停止。

若 `$ROOT` 不在主工作树而 `$KB` 不存在，**停止并要求先在主工作树初始化**。不能在 linked worktree 内建立根知识库，否则会把功能分支误记成主线 anchor。主工作树首次运行时才允许进入新建/远端 bootstrap 选择。

⚠️ linked worktree 中的 `<ROOT>/docs/knowledge` 不存在是正常现象；只判断已经解析的 `$KB`。短期分支默认只读，写入归属见 [branch-resolution.md](branch-resolution.md)。

## 目录结构

```
<ROOT>/docs/knowledge/         # 独立 git 仓库
├── .git/                      # 知识库自己的版本历史
├── .gitignore                 # 必须含 .lock* —— 见下方"跨机器同步"
├── _identity.json             # 项目身份锚点（静态）
├── _meta.json                 # anchor_kind + 主线分支实际名（静态，极少变）
├── _branches.json             # 分支写入归属登记（静态，按原始分支名索引）
├── _sync.json                 # 主线的同步状态（每次运行必变，与内容分离）
├── .lock                      # 并发锁（不提交）
├── INDEX.md                   # 主线索引 ← 最常访问的路径
├── domains/ shared/ decisions/ integrations/ workflows/ pitfalls/
├── .branches/
│   └── <branch-slug>/         # 长功能分支的自包含知识快照
│       ├── _meta.json         # 原始分支名 + forked_from
│       ├── _sync.json         # 该分支自己的同步状态 —— 不与主线共用
│       ├── _deleted           # 本分支删除的条目路径清单，一行一个
│       ├── INDEX.md           # 只链接本快照内条目
│       └── ...                # fork 时复制的六类条目，之后在分支内更新
└── .archive/                  # 已合并或已废弃的分支目录
```

`.branches/` 和 `.archive/` 点开头，人浏览 `docs/knowledge/` 时天然不碍事。

**完整快照语义**：建立 `independent` 分支目录时，先记录知识库当前 HEAD 为 `forked_from`，再把主线 `INDEX.md` 与六类条目完整复制进分支目录（不复制任何控制文件）。这样分支 INDEX 的相对链接只指向本目录，clone、归档和离线阅读都自洽；代价是长分支会复制一份 Markdown，优先换取正确性。

分支删除 base 已存在的条目时，既移除快照文件又写入 `_deleted`。base 中存在、分支快照里缺失、但 `_deleted` 未登记属于损坏，Phase M 必须停止；同一路径同时存在文件和 `_deleted` 记录也必须停止。

**扫描用白名单，不用黑名单。** 生成 INDEX、统计条目、检查互链时只扫六个分类目录和 `INDEX.md`；`.branches/`、`.archive/`、`.git/` 一律不进入。将来新增顶层目录时，白名单不会把它意外收录成当前知识。

## 主仓库侧的前置检查

**① `.gitignore` 必须包含它，且路径要带上 scope。** gitignore 的 `/` 开头模式是**相对该 .gitignore 文件所在目录**的，所以在仓库根写 `/docs/knowledge/` **匹配不到** `apps/web/docs/knowledge/`：

| `<ROOT>` | 写在哪 | 写什么 |
|---|---|---|
| 仓库根 | 仓库根 `.gitignore` | `/docs/knowledge/` |
| `apps/web` | 仓库根 `.gitignore` | `/apps/web/docs/knowledge/` |
| `apps/web` | `apps/web/.gitignore` | `/docs/knowledge/` |

推荐第二行——单一 `.gitignore` 便于维护，也不必为子项目新建文件。**写完必须同时验证 ignore 生效且父仓库索引不再追踪知识库**：

```bash
git -C "$MAIN" check-ignore -q "${SCOPE_REL}docs/knowledge"
test -z "$(git -C "$MAIN" ls-files -- "${SCOPE_REL}docs/knowledge")"
```

任一失败都必须**停止写入**。`.gitignore` 不会自动清理已经进入索引的文件；需要用户确认后执行 `git rm -r --cached -- <scope>/docs/knowledge` 并提交主仓库。继续运行可能把会话提炼出的内部判断带进公开仓库。

首次由 Codewise 添加 ignore 规则后也要立即停下，让用户先提交主仓库变更，再从 Phase 0 重跑。不要一边生成 KB 一边把这条主仓库改动豁免出源码快照。

**② 知识库必须是独立 git 仓库。** 不只要求 `show-toplevel == <KB>`：`<KB>/.git` 必须是自身拥有的真实目录（不是 symlink 或 linked-worktree gitfile），且规范化 `--git-common-dir` 必须等于它。否则后续 commit/refs/push 可能改到外部共享仓库。目录存在但不满足 = 旧格式或危险布局，见下方守卫。

git 对嵌套仓库有内置保护：`git clean -xfd` 会打印「跳过仓库 docs/knowledge」而不删它，只有双 `f`（`-xffd`）才会。这层保护是免费的，但别依赖它——本地镜像才是真正的兜底。

## 项目身份校验

`<KB>/_identity.json`：

```json
{
  "root_commits": ["<git rev-list --max-parents=0 HEAD 的全部结果>"],
  "scope_root": "<ROOT 相对仓库根；仓库根写 .>",
  "remote_url": "<git remote get-url origin，仅供人工识别>",
  "project_name": "<仓库目录名>"
}
```

身份锚点用**首个 commit 的 hash**，不用 remote URL——仓库改名、迁移托管、换 remote 都不影响它。合并过独立历史的仓库有多个 root commit，全部记录；运行时要求 root commit 集合与记录值完全相等。若后来合法引入了另一段独立历史，走显式 `reidentify`，不能靠“有一个相同”静默放行。

每次运行开始就校验 `root_commits` 与 `scope_root`。任一不匹配时**硬失败**并打印双方实际值，不要自动放行——同一 monorepo 的两个子项目共享 root commits，只靠提交锚点会串库。合法迁移（换托管、仓库重建）由用户显式传 `reidentify` 参数覆盖。

Git 仓库尚无 commit 时没有 root commit 可作锚点，临时使用与非 Git 模式相同的 `weak_path_sha256` + `scope_root`，并披露它不可跨路径/跨机器移植。产生首个 commit 后必须显式 `reidentify` 升级为 `root_commits`，不能继续沿用弱锚点。

## 同步状态与元信息

`baseline_commit`、`synced_at` 这些**每次运行必变**的字段放在 **`<KBR>/_sync.json`**，**不放 INDEX.md**：

```json
{
  "codewise_version": 3,
  "baseline_commit": "<主仓库 git rev-parse HEAD>",
  "synced_at": "<完整 ISO 8601，精确到秒含时区>",
  "scope_root": "<ROOT 相对仓库根的路径>",
  "multi_codetree": "<有效代码树清单>",
  "session_sources": "<来源:数量>",
  "worktree_count": 0,
  "known_worktrees": ["<相对主工作树的路径>"]
}
```

**为什么必须分离**：这两个字段每次运行都变，放在 INDEX.md 里意味着两台机器各跑一次后，merge 时**必然**在这两行冲突——不是概率问题，是每次都会。分离后 INDEX.md 的合并退化成纯内容合并，多数情况 git 能自动处理；顺带 INDEX.md 也不再每次运行都产生 diff。

`_meta.json` 是**静态**信息，极少变，所以不与 `_sync.json` 合并——否则又把静态内容拖进高频变更文件。Git 根使用 `{"anchor_kind":"main","branch":"<用户确认的实测 anchor>"}`；非 Git 根使用 `{"anchor_kind":"non-git","degraded_acknowledged":true}`，不得伪造 `main` 分支。

⚠️ **`_sync.json` 跟 `<KBR>` 走，不是 `<KB>`。** 主线一份、每个分支目录各一份。放在 `<KB>` 根会让所有分支共用同一个 `baseline_commit`——功能分支写入自己的 HEAD 后，切回主线就会命中"主仓库落后于知识库"检测并建议 `git pull`，**而那个 commit 在另一条分支上，pull 根本没用**。

### 冲突解决规则

两台机器各自跑过后 `_sync.json` 分叉，按以下规则取值：

| 字段 | 规则 | 理由 |
|---|---|---|
| `baseline_commit` | **取更早的**（`git merge-base --is-ancestor A B` 成功则 A 更早；无祖先关系时取两者的 `merge-base`） | 宁可重扫一段，不可漏扫 |
| `synced_at` | **取更早的** | 同上，会话提取边界宁可前移 |
| `known_worktrees` | **取并集** | 它本就是只增不减的累积集合 |
| 其余披露字段 | 取任一 | 只用于报告，不影响正确性 |

## 并发锁

`<KB>/.lock` 记录不可猜的随机 `token`、`hostname`、`branch` 与 `started_at`。一次性 helper PID 不能代表整个 Agent 流程，绝不能用“helper 已退出”自动判陈旧。获取失败时打印占用者与锁龄并拒绝运行。

所有会写 KB 的模式（无论是否有 origin）都必须在任何 KB 修改前运行：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py acquire '<KB>' --branch '<当前分支>'
```

脚本使用 `O_CREAT|O_EXCL` 原子获取，不能自行实现“先检查文件不存在再写”的竞态流程。从 JSON 保存 `token` 为 `LOCK_TOKEN`；整个 fetch/reconcile、生成、commit、mirror、push 都持锁，并在 finally 中释放：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py release '<KB>' --token "$LOCK_TOKEN"
```

错误 token 不能释放别人的锁。中途失败也必须释放；正常流程绝不自行 `rm` 锁文件。

**`.lock*` 必须写进 `<KB>/.gitignore`。** 这同时覆盖锁、cleanup guard 与隔离文件，避免异常状态被 `git add -A` 提交并 push。

陈旧锁不会被 `acquire` 隐式抢占。确认占用流程已终止、锁龄超过 2 小时，并从现有锁取得其 owner token 后，才运行：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py clear-stale '<KB>' \
  --token '<现有 owner token>' --timeout-seconds 7200
```

`clear-stale` 以第二个原子 cleanup guard 排除 release/acquire 竞态。若清理进程崩溃，读取 `.lock.break` 的 cleanup token；确认 guard 也超过超时后用下列命令恢复。它只保留或还原原 owner 锁，不会静默丢锁，然后再重试 `clear-stale`：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py recover-cleanup '<KB>' \
  --token '<cleanup token>' --timeout-seconds 7200
```

**锁只需覆盖同一台机器上的多个 Worktree。** 跨机器时两台各持有自己 clone 的知识库仓库，锁是本地的、天然不冲突；跨机器的真冲突发生在 push 阶段，git 自己会拒绝 non-fast-forward。

## 跨机器 bootstrap 与同步

完整顺序固定为：**bootstrap → fetch/reconcile → update → commit → mirror → push**。不得只在最后盲目 push。

### Bootstrap

主工作树里的 `$KB` 不存在时，不直接 `git init`。先展示物理主工作树当前分支、`refs/remotes/origin/HEAD`（若存在）及 HEAD；detached/无分支时停止。然后检查 scope 根的 `.codewise-bootstrap.json`：

```bash
python3 <SKILL_DIR>/scripts/resolve_bootstrap_hint.py '<ROOT>' --kb '<KB>' --pretty
```

该文件只有在被源码仓库追踪、与 HEAD 完全一致、无软链、schema/路径/remote/分支均通过校验时才可信。合法 hint 是项目维护者预先登记的 remote 与主线 anchor：展示 hint 与上述 Git 证据；当前分支和 `origin/HEAD`（存在时）都与 `source_branch` 一致即可直接走 existing remote bootstrap，不再向用户重复询问。任一证据不一致时停止，不能自动改 checkout 或猜主线。hint 缺失或无效时不消费其中任何字段，remote default 也只作证据，用户必须明确确认主线 anchor。然后展示三个选择：

1. 提供已有 knowledge remote（新机器/重新 clone）；
2. 明确初始化一个新知识库；
3. 取消。

选择后，先为最终路径获取独立 bootstrap 锁；它位于稳定的本机临时锁目录，因此 `$KB` 和父目录都可尚不存在：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py acquire '<KB>' --bootstrap --branch '<anchor>'
```

保存 `BOOTSTRAP_TOKEN`。在 bootstrap 锁内，才创建 `$KB` 同一文件系统/父目录下的临时 KB。已有 remote clone 到临时目录，checkout、`git fsck --connectivity-only`，再运行 tree/control guard 并校验 `_identity.json` 的 root commits 与 scope；新库也先在临时目录完成 init、身份与安全结构，并写入可验证的最小控制状态：上述 Git/非 Git `_meta.json`、空对象 `_branches.json`、以及 `baseline_commit:null`、`synced_at:null`、实测 `scope_root` 和空披露字段的 schema v3 `_sync.json`。通过 control validator 后，才在临时 KB **预先获取内部锁**：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py acquire '<临时 KB>' --branch '<anchor>'
```

保存 `LOCK_TOKEN`；带着 `.lock` 原子 rename 临时 KB 为最终 `$KB`，再释放 bootstrap 锁并继续持有内部锁：

```bash
python3 <SKILL_DIR>/scripts/kb_lock.py release '<KB>' \
  --bootstrap --token "$BOOTSTRAP_TOKEN"
```

这样已有模式永远不会看见“已出现但尚未上锁”的 KB。

失败时按 token 释放已取得的内部/bootstrap 锁并清理临时目录，最终 `$KB` 保持不存在。新库第一次推送使用普通 `git push -u origin HEAD`，绝不 force。正常退出 finally 释放最终 `$KB` 内部锁。

知识库自身的 remote 地址无法从被 ignore 的主仓库自动发现。跨机器自动 bootstrap 必须由源码仓库 HEAD 中的 `.codewise-bootstrap.json` 显式登记；没有合法 hint 时仍必须由用户或本机安全配置提供，绝不猜仓库名。公开 hint 禁止内嵌凭据，只允许 `https://`、`ssh://` 或 `git@host:path` remote。

### 运行前 fetch/reconcile

拿锁后要求 KB 工作树干净；有 `origin` 时先 fetch，并在读取远端内容前验证远端 `_identity.json`。然后分类：

- 相同提交：继续；
- 本地落后：`--ff-only` 快进；
- 远端落后：保留本地，继续；
- 已分叉：先创建本地 safety ref 并推入 mirror，再做普通三方 merge。

静态 `_identity.json`、`_meta.json` 或 `scope_root` 不一致时硬停止。内容条目冲突不自动选 ours/theirs；保留 safety ref、本地提交和 mirror 后停下等待用户裁决。以下控制文件按语义合并：

- 每个 `<KBR>/_sync.json`：`baseline_commit` 取主项目历史中更早的祖先；无祖先关系取主项目 `merge-base`；任一提交不可解析则置 `null` 触发全扫。`synced_at` 取更早值，`known_worktrees` 取并集，scope/schema 不兼容则停止。
- `_branches.json`：按原始分支名取 key 并集；同一个 key 的归属不同则停止。union 后还必须校验所有 `independent.slug` 全局唯一，并逐目录核对 `_meta.json.branch` 等于登记的原始分支名；任一冲突停止，不能让两个分支共享目录。
- `_deleted`：仅对合法条目路径取集合并集。
- `INDEX.md`：内容 reconcile 完成后重建，不手选一边。

fetch 失败时允许继续本地更新，但报告 `remote freshness unknown`，不得声称已同步。

### Commit、mirror 与 push

知识库写完全部条目和元信息后自动 commit，message 带 baseline 短码与条目变更数。remote `mirror` 指向本机裸仓库（建议 `${CODEWISE_BACKUP_HOME:-~/.local/share/codewise-backup}/<project>-<scope>.git`）；它无网络依赖，但仍可能因磁盘、权限或损坏失败，必须如实报告，不能声称“零失败”。

先推 mirror，再普通 push origin。non-fast-forward 时重新 fetch、执行同一 reconcile 后重试，最多 2 次；仍竞争或遇到内容冲突就停止。**禁止 `--force` 与 `--force-with-lease`**。本地 commit、mirror 与 safety ref均保留，不能 reset 掉唯一副本。

**远端仓库是可选增强，不是方案的必需部分。** 容灾已由本地镜像覆盖；远端只服务**跨机器同步**。所以：

- 检测到 `<KB>` 没有 `origin` remote 时，**在报告里提示一次**并给出可直接粘贴的命令，不自动创建（创建远程仓库是不可逆的外部操作）：

  ```
  未配置远端（仅本地镜像）。需要跨机器同步时：
    gh repo create <project>-knowledge --private
    git -C <KB> remote add origin git@github.com:<user>/<project>-knowledge.git
  ```

- 已配置 `origin` 时按上述 fetch/reconcile/push 流程运行；失败不回滚本地内容，但必须报告准确状态和恢复入口。

**跨机器同步需要两条腿**：知识库走 git remote，**会话目录走文件同步**。要同步的是**全部已用来源**的目录，不只 Claude：

```
~/.claude/projects/                    # Claude Code
~/.codex/sessions/                     # Codex CLI / Desktop
~/.codex/archived_sessions/            # Codex 归档
~/.gemini/tmp/                         # Gemini CLI（如在用）
```

漏掉 Codex 那两个目录是个实际风险——实测某些项目的 Codex 会话数比 Claude 还多。只做前者的话，另一台机器的会话仍然扫不到，那边的知识库会缺掉最有价值的部分。做之前先想清楚是否两条都要。

跨机器还有三处必须注意：

1. **`.lock*` 不能进版本库**（见上方并发锁）
2. **主仓库可能落后于知识库** — 拉了 KB 但没拉主仓库时，`_sync.json` 里的 `baseline_commit` 是当前 HEAD 的"未来"。先判双向祖先关系：`HEAD` 是 baseline 祖先才提示同步主仓库；两者分叉则按 Phase U.1 的 rebase/分叉流程处理，不能把所有失败都误报成 `git pull`。
3. **`known_worktrees` 的相对路径在另一台可能指向别的东西** — 概率极低（需同名且同相对位置），且 stale Worktree 的会话仍要过内容过滤，影响可控；但报告里应如实标注这类条目"在本机不存在"

会话目录的文件同步必须启用 **keep-both 或 versioning**。UUID 通常不同，但同一个会话可在两台机器续写并保留同名 UUID；last-writer-wins 仍会丢记录，不能宣称天然无冲突。

一个项目一个知识库仓库，不要多项目共用——分支目录的归属推导依赖单一分支拓扑。

隐私边界不因仓库私有而放松：条目只写语义结论与相对路径，不写密钥、令牌、含用户名的绝对路径、原始用户消息。私有仓库转公开只需点一下，而这条规则的成本几乎为零。

## 旧格式守卫

判据是**已统一解析的 `$KB` 是不是独立 git 仓库**：

```bash
# 注意：不能用 rev-parse --git-dir —— 它在主仓库追踪的普通子目录里同样成功
# （向上找到父仓库的 .git），会把旧格式误判成新格式。必须比较仓库根和自有 common dir：
top=$(git -C "$KB" rev-parse --show-toplevel 2>/dev/null)
common=$(git -C "$KB" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
[ "$top" = "$(cd "$KB" && pwd -P)" ]
[ -d "$KB/.git" ] && [ ! -L "$KB/.git" ]
[ "$common" = "$(cd "$KB/.git" && pwd -P)" ]
```

| 情况 | 处理 |
|---|---|
| 目录不存在 | 首次生成，正常流程 |
| 存在且是独立 git 仓库 | 新格式，正常运行 |
| **存在但不是 git 仓库** | ⛔ **停止**，报告需要人工迁移，不做任何写入 |

第三种是旧版把知识库当普通文件提交进主仓库留下的产物。当成"首次生成"会重新全库扫描并丢弃已有的手写条目。**迁移不在本 skill 范围内**——它需要人工判断这些：

- **选源**：多个 Worktree / 多个分支里的旧版本内容可能不同，要先确认哪份是权威、有没有对方独有的条目
- **本地与远端都要处理**：只在本地分支 `git rm --cached` 不够。**远端分支若仍追踪该目录，下次 pull 就把它带回索引**，迁移等于白做
- **共享分支要等 pull 到最新再做**：在落后远端 N 个提交的基础上改动，push 会被拒，而且替用户决定了合并时机。这类分支应由用户自己在合适时机处理
- **切到仍在追踪的分支时会覆盖工作树**：git 会把追踪版本写进 `docs/knowledge/`（对主仓库而言那是 gitignore 的可丢弃文件）。数据不会丢——独立仓库的 `.git` 不受影响，`git -C docs/knowledge checkout .` 即可恢复——但要事先知道，否则会看到一堆莫名其妙的变动
- **元信息带过**：从旧 INDEX.md 的元信息区抽出字段写进 `_sync.json`，并剥离该区

即使 `$KB` 是独立仓库，也必须执行前述 `check-ignore` + `ls-files` 双检查；任一失败就阻塞写入。

## 非 git 降级模式

主仓库不是 git 仓库时，`<KB>` 仍取 `<ROOT>/docs/knowledge`，它自己 `git init`，但：

- 无法建立 `_identity.json` 的 root_commits 锚点（改用字段 `weak_path_sha256`，值为规范化 `<ROOT>` 绝对路径的 SHA-256 前 16 位，并保留 `scope_root: "."`；明确标注为弱锚点）
- 无 `.gitignore` 可写（没有主仓库要忽略它）

降级模式下**不做**以下事情，报告里要明确说出来：

- 无 `known_worktrees` 打捞（没有 Worktree 概念）
- 无分支目录、无归属推导（没有分支）
- 无三方合并（没有 `forked_from` 可依据）
- 无 `baseline_commit` 差量，退化为 mtime 边界
- 弱路径身份不可跨路径/跨机器 bootstrap；需要可移植同步时先给主项目建立 Git 历史

**首次运行遇到非 git 主仓库时，应当询问用户是否 `git init`**（见 SKILL.md Phase 0）——这是能力级差异，且首次做成本最低；等知识库积累几个月后再补，之前那段演变就没有基准了。用户拒绝后根元信息固定为 `{"anchor_kind":"non-git","degraded_acknowledged":true}`，不包含 `branch`；`_branches.json` 必须为空，`_sync.json.scope_root` 固定为 `.`。第二次运行继续按这一 schema 校验，不能暗造不存在的 Git 主线。

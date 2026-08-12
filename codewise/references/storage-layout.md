# 知识库存储布局

知识库放在 `<ROOT>/docs/knowledge/`，但它**不是主仓库的内容**——主仓库把它 gitignore 掉，它自己是一个独立的 git 仓库。

这样同时拿到三件事：位置直观（人能看见、能搜索，Agent 直接 Read 无需先跑命令）、有自己的版本历史（三方合并的前提）、且完全不参与主仓库的分支与合并。

## 目录

1. 路径解析
2. 目录结构
3. 主仓库侧的两项前置
4. 项目身份校验
5. 并发锁
6. 提交与推送
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

### Worktree 里的定位

`docs/knowledge/` 被 gitignore，所以**新建的 worktree 里没有它**。worktree 中要访问知识库，先定位主工作树：

```bash
MAIN=$(git worktree list --porcelain | head -1 | cut -d' ' -f2)
# 知识库在 $MAIN/docs/knowledge/
```

这是唯一需要跑命令的场景。worktree 里默认**只读不写**（短期实验分支不写知识库，见 branch-resolution.md）。

## 目录结构

```
<ROOT>/docs/knowledge/         # 独立 git 仓库
├── .git/                      # 知识库自己的版本历史
├── .gitignore                 # 必须含 .lock —— 见下方"跨机器同步"
├── _identity.json             # 项目身份锚点（静态）
├── _meta.json                 # anchor_kind + 主线分支实际名（静态，极少变）
├── _sync.json                 # 上次同步状态（每次运行必变，与内容分离）
├── .lock                      # 并发锁（不提交）
├── INDEX.md                   # 主线索引 ← 最常访问的路径
├── domains/ shared/ decisions/ integrations/ workflows/ pitfalls/
├── .branches/
│   └── <branch-slug>/         # 长功能分支的稀疏知识库
│       ├── _meta.json         # 原始分支名 + forked_from
│       ├── _deleted           # 本分支删除的条目路径清单，一行一个
│       └── ...                # 只放本分支新增或修改过的条目
└── .archive/                  # 已合并或已废弃的分支目录
```

`.branches/` 和 `.archive/` 点开头，人浏览 `docs/knowledge/` 时天然不碍事。

**稀疏语义**：分支目录 fork 时是**空的**（只有 `_meta.json`），之后只落地本分支新增或改动过的条目。未改动的条目不复制——它们从主线读取，因此永远是最新版，不会读到 fork 时的陈旧快照。修改过的条目**存全文**，不存 diff。

分支目录里"文件不存在"意味着"继承主线"，所以删除必须显式写进 `_deleted`，否则删掉的条目会复活。

**扫描用白名单，不用黑名单。** 生成 INDEX、统计条目、检查互链时只扫六个分类目录和 `INDEX.md`；`.branches/`、`.archive/`、`.git/` 一律不进入。将来新增顶层目录时，白名单不会把它意外收录成当前知识。

## 主仓库侧的两项前置

**① `.gitignore` 必须包含它。** 首次生成时检查 `<ROOT>` 或仓库根的 `.gitignore`，缺失就追加（相对仓库根的路径）：

```
/docs/knowledge/
```

漏了这条，知识库会被主仓库追踪——那就退回了旧方案的全部问题：分支冲突、工作树每次变脏、合并时 INDEX 元信息必冲突，以及**主仓库公开时知识库里的调试过程和内部判断一并公开**。

**② 知识库必须是独立 git 仓库。** 目录存在但不是 git 仓库 = 旧格式残留，见下方守卫。

git 对嵌套仓库有内置保护：`git clean -xfd` 会打印「跳过仓库 docs/knowledge」而不删它，只有双 `f`（`-xffd`）才会。这层保护是免费的，但别依赖它——本地镜像才是真正的兜底。

## 项目身份校验

`<KB>/_identity.json`：

```json
{
  "root_commits": ["<git rev-list --max-parents=0 HEAD 的全部结果>"],
  "remote_url": "<git remote get-url origin，仅供人工识别>",
  "project_name": "<仓库目录名>"
}
```

身份锚点用**首个 commit 的 hash**，不用 remote URL——仓库改名、迁移托管、换 remote 都不影响它。合并过独立历史的仓库有多个 root commit，全部记录，取交集非空即认为匹配。

每次运行开始就校验。不匹配时**硬失败**并打印双方实际值，不要自动放行——这通常意味着 `docs/knowledge/` 被复制到了别的项目，继续写入会污染错误项目的知识库。合法迁移（换托管、仓库重建）由用户显式传 `reidentify` 参数覆盖。

## 同步状态与元信息

`baseline_commit`、`synced_at` 这些**每次运行必变**的字段放在 `<KB>/_sync.json`，**不放 INDEX.md**：

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

`_meta.json`（`anchor_kind` + 主线分支名）是**静态**信息，极少变，所以不与 `_sync.json` 合并——否则又把静态内容拖进高频变更文件。

### 冲突解决规则

两台机器各自跑过后 `_sync.json` 分叉，按以下规则取值：

| 字段 | 规则 | 理由 |
|---|---|---|
| `baseline_commit` | **取更早的**（`git merge-base --is-ancestor A B` 成功则 A 更早；无祖先关系时取两者的 `merge-base`） | 宁可重扫一段，不可漏扫 |
| `synced_at` | **取更早的** | 同上，会话提取边界宁可前移 |
| `known_worktrees` | **取并集** | 它本就是只增不减的累积集合 |
| 其余披露字段 | 取任一 | 只用于报告，不影响正确性 |

## 并发锁

`<KB>/.lock` 记录 `pid`、`hostname`、`branch`、`started_at`。获取失败时打印占用者信息并拒绝运行。

**`.lock` 必须写进 `<KB>/.gitignore`。** 否则异常退出留下的锁会被 `git add -A` 提交并 push——另一台机器 pull 后看到一个 hostname 不同的锁，查不了 PID，只能走超时判定，**两小时内拒绝运行**。

陈旧锁：`hostname` 与本机相同时检查 PID 是否存活，不存在则直接清除；跨主机或无法检查时用超时判定（建议 2 小时）。

**锁只需覆盖同一台机器上的多个 Worktree。** 跨机器时两台各持有自己 clone 的知识库仓库，锁是本地的、天然不冲突；跨机器的真冲突发生在 push 阶段，git 自己会拒绝 non-fast-forward。

## 提交与推送

知识库每次更新结束后自动 commit（**在写完所有条目与元信息之后**，顺序同 U.6）。commit message 带 baseline 短码与条目变更数。

**本地镜像（必须成功）** — remote `mirror` 指向 `~/.claude/codewise-backup/<project-name>.git`（裸仓库，不存在则创建），每次 commit 后推送。零网络依赖、零失败可能。它兜住的失败模式是「主仓库被删除或重新 clone 时知识库一起消失」。

**远端仓库是可选增强，不是方案的必需部分。** 容灾已由本地镜像覆盖；远端只服务**跨机器同步**。所以：

- 检测到 `<KB>` 没有 `origin` remote 时，**在报告里提示一次**并给出可直接粘贴的命令，不自动创建（创建远程仓库是不可逆的外部操作）：

  ```
  未配置远端（仅本地镜像）。需要跨机器同步时：
    gh repo create <project>-knowledge --private
    git -C <KB> remote add origin git@github.com:<user>/<project>-knowledge.git
  ```

- 已配置 `origin` 时每次 commit 后尝试 push，**失败绝不阻塞流程**，只在报告里留一行「远端未同步，本地镜像已保存，N 个 commit 待推送」

**跨机器同步需要两条腿**：知识库走 git remote，**会话目录走文件同步**（syncthing / rsync `~/.claude/projects/`）。只做前者的话，另一台机器的会话仍然扫不到，那边的知识库会缺掉最有价值的部分。做之前先想清楚是否两条都要。

跨机器还有三处必须注意：

1. **`.lock` 不能进版本库**（见上方并发锁）
2. **主仓库可能落后于知识库** — 拉了 KB 但没拉主仓库时，`_sync.json` 里的 `baseline_commit` 是当前 HEAD 的"未来"。此时 `merge-base baseline HEAD` 会退到更早的点，差量范围为空，codewise **静默什么都不做**。所以运行前必须检测 `git merge-base --is-ancestor <baseline> HEAD`，失败就提示先 `git pull` 主仓库（见 SKILL.md Phase 0）
3. **`known_worktrees` 的相对路径在另一台可能指向别的东西** — 概率极低（需同名且同相对位置），且 stale Worktree 的会话仍要过内容过滤，影响可控；但报告里应如实标注这类条目"在本机不存在"

会话文件本身跨机器同步是**安全**的：文件名是 UUID，每台机器产生自己的，双向同步不会冲突，代价只是每台都有全量副本。

一个项目一个知识库仓库，不要多项目共用——分支目录的归属推导依赖单一分支拓扑。

隐私边界不因仓库私有而放松：条目只写语义结论与相对路径，不写密钥、令牌、含用户名的绝对路径、原始用户消息。私有仓库转公开只需点一下，而这条规则的成本几乎为零。

## 旧格式守卫

判据是**`<ROOT>/docs/knowledge/` 是不是独立 git 仓库**：

```bash
git -C <ROOT>/docs/knowledge rev-parse --git-dir 2>/dev/null
```

| 情况 | 处理 |
|---|---|
| 目录不存在 | 首次生成，正常流程 |
| 存在且是独立 git 仓库 | 新格式，正常运行 |
| **存在但不是 git 仓库** | ⛔ **停止**，报告需要人工迁移，不做任何写入 |

第三种是旧版把知识库当普通文件提交进主仓库留下的产物。当成"首次生成"会重新全库扫描并丢弃已有的手写条目。迁移涉及选源（多个 Worktree 里的旧版本可能不同）、旧文件从**所有存活分支**上删除（否则长期分支合并回来时会复活）、元信息带过——需要人工判断，不在本 skill 范围内。

另外检查 `.gitignore`：目录已是独立仓库但**没被 gitignore**，说明它正在被主仓库追踪。此时**警告并提示补 gitignore + `git rm -r --cached`**，不阻塞（知识库本身是好的，只是主仓库多追踪了一份）。

## 非 git 降级模式

主仓库不是 git 仓库时，`<KB>` 仍取 `<ROOT>/docs/knowledge`，它自己 `git init`，但：

- 无法建立 `_identity.json` 的 root_commits 锚点（用 `<ROOT>` 绝对路径的 SHA-256 前 16 位代替，并标注为弱锚点）
- 无 `.gitignore` 可写（没有主仓库要忽略它）

降级模式下**不做**以下事情，报告里要明确说出来：

- 无 `known_worktrees` 打捞（没有 Worktree 概念）
- 无分支目录、无归属推导（没有分支）
- 无三方合并（没有 `forked_from` 可依据）
- 无 `baseline_commit` 差量，退化为 mtime 边界

**首次运行遇到非 git 主仓库时，应当询问用户是否 `git init`**（见 SKILL.md Phase 0）——这是能力级差异，且首次做成本最低；等知识库积累几个月后再补，之前那段演变就没有基准了。用户拒绝则记入 `_meta.json` 的 `degraded_acknowledged`，之后不再重复询问。

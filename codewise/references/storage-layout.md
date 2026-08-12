# 知识库存储布局

知识库不放在主仓库的工作树里，而是一个**独立的 git 仓库**，挂在主仓库 `git-common-dir` 下。这样它既有自己的版本历史（三方合并的前提），又不会参与主仓库任何分支的 merge——主仓库切分支、合并、rebase 都碰不到它。

## 目录

1. 路径解析
2. 目录结构
3. 项目身份校验
4. 并发锁
5. 提交与推送
6. 旧格式守卫
7. 非 git 降级模式

## 路径解析

```bash
# 必须带 --path-format=absolute。不带时主工作树返回相对路径 ".git"，
# 而 worktree 里返回绝对路径——主树和 worktree 会安静地读写两个不同位置，
# 这类 bug 极难查。
KB=$(git -C <ROOT> rev-parse --path-format=absolute --git-common-dir)/codewise
```

`git-common-dir` 对同一项目的所有 Worktree 是**同一个值**，所以主工作树和任意 Worktree 天然共享同一份知识库，不需要任何同步机制。

同一仓库内的多个子项目各有独立知识库，按 scope 分隔：

| 占位符 | 取值 |
|---|---|
| `<KB>` | `$(git rev-parse --path-format=absolute --git-common-dir)/codewise` |
| `<SCOPE>` | `<ROOT>` 相对仓库根的路径；`.` 映射为 `_root`，其余把 `/` 替换为 `-`（`apps/web` → `apps-web`） |
| `<KBR>` | 本次实际读写的知识库根，见下方"归属分支"决定 |

`<KBR>` 由当前分支决定（推导规则见 [branch-resolution.md](branch-resolution.md)）：

- 归属主线 → `<KB>/<SCOPE>/main`
- 归属某个有独立目录的分支 → `<KB>/<SCOPE>/branches/<branch-slug>`

`<branch-slug>` 把分支名里非 `[A-Za-z0-9._-]` 的字符替换为 `-`。slug 可能与另一个分支撞车（`feat/big` 与 `feat-big`），所以每个分支目录的 `_meta.json` 必须记录**原始分支名**；发现 slug 已被另一个分支占用时报错停止，不要覆盖。

**`main/` 同样要有 `_meta.json`。** 目录名固定叫 `main`，但仓库的主线分支可能叫 `master`、`trunk` 或别的——首次生成时把当时的分支名写进去，之后归属推导从这里读。**不要把字符串 `"main"` 当分支名**，那在 `master` 仓库上会静默失配。

## 目录结构

```
<KB>/
├── _identity.json            # 项目身份锚点，仓库级
├── .lock                     # 并发锁，仓库级
└── <SCOPE>/
    ├── main/                 # 主线知识库：INDEX.md + 各分类目录
    │   └── _meta.json        # anchor_kind + 主线分支的**实际名字**（可能是 master）
    ├── branches/
    │   └── <branch-slug>/    # 长功能分支的稀疏知识库
    │       ├── _meta.json    # 原始分支名 + forked_from
    │       ├── _deleted      # 本分支删除的条目路径清单，一行一个
    │       └── ...           # 只放本分支新增或修改过的条目
    └── archive/              # 已合并或已废弃的分支目录
```

**稀疏语义**：分支目录 fork 时是**空的**（只有 `_meta.json`），之后只落地本分支新增或改动过的条目。未改动的条目不复制——它们从 `main/` 读取，因此永远是主线最新版，不会读到 fork 时的陈旧快照。修改过的条目**存全文**，不存 diff。

分支目录里"文件不存在"意味着"继承 `main/`"，所以删除必须显式写进 `_deleted`，否则删掉的条目会从 `main/` 复活。

**扫描用白名单，不用黑名单。** 生成 INDEX、统计条目、检查互链时只扫 `main/` 和当前分支目录；`archive/` 与其他分支目录一律不进入。将来新增顶层目录时，白名单不会把它意外收录成当前知识。

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

每次运行开始就校验。不匹配时**硬失败**并打印双方实际值，不要自动放行——这种情况通常意味着 `.git/codewise/` 被复制到了别的仓库，继续写入会污染错误项目的知识库。合法迁移（换托管、仓库重建）由用户显式传 `reidentify` 参数覆盖。

## 并发锁

`<KB>/.lock` 记录 `pid`、`hostname`、`branch`、`started_at`。获取失败时打印占用者信息并拒绝运行。

陈旧锁：`hostname` 与本机相同时检查 PID 是否存活，不存在则直接清除；跨主机或无法检查时用超时判定（建议 2 小时）。

**锁只需覆盖同一台机器上的多个 Worktree。** 跨机器时两台各持有自己 clone 的知识库仓库，锁是本地的、天然不冲突；跨机器的真冲突发生在 push 阶段，git 自己会拒绝 non-fast-forward。

## 提交与推送

知识库每次更新结束后自动 commit（**在写完所有条目与元信息之后**，顺序同 U.6）。commit message 带 baseline 短码与条目变更数。

推送分两层：

1. **本地镜像（必须成功）** — remote 指向 `~/.claude/codewise-backup/<project-name>.git`（裸仓库，不存在则创建），每次 commit 后推送。零网络依赖、零失败可能。它的作用是兜住"主仓库被删除或重新 clone 时 `.git/codewise/` 一起消失"这个失败模式。
2. **GitHub 私有仓库（尽力）** — 每次 commit 后尝试 push，服务跨机器同步与异地容灾。**失败绝不阻塞流程**，只在报告里留一行「远端未同步，本地镜像已保存」。

一个项目一个知识库仓库（`<project>-knowledge`），不要多项目共用一个仓库——分支目录的归属推导依赖单一分支拓扑，多项目共用会让它无法成立。

隐私边界不因仓库私有而放松：条目只写语义结论与相对路径，不写密钥、令牌、含用户名的绝对路径、原始用户消息。私有仓库转公开只需点一下，而这条规则的成本几乎为零。

## 旧格式守卫

`<ROOT>/docs/knowledge/INDEX.md` 存在、而 `<KB>/<SCOPE>/main/INDEX.md` 不存在时：**停止，报告需要人工迁移，不做任何写入。**

不能当作"首次生成"——那会重新全库扫描并忽略已有的手写条目。迁移涉及选源（多个 Worktree 里的旧知识库版本可能不同）、旧位置处理（必须在**所有存活分支**上删除，否则长期分支合并回来时旧目录会复活）、元信息带过，这些需要人工判断，不在本 skill 范围内。

## 非 git 降级模式

不是 git 仓库时，`<KB>` 取 `~/.claude/codewise/<abs-path-hash>/`，其中 hash 是 `<ROOT>` 绝对路径的 SHA-256 前 16 位。

降级模式下**不做**以下事情，报告里要明确说出来：

- 无 `known_worktrees` 打捞（没有 Worktree 概念）
- 无分支目录、无归属推导（没有分支）
- 无三方合并（没有 `forked_from` 可依据）
- 无 `baseline_commit` 差量，退化为 mtime 边界
- 无自动 commit / push（不是 git 仓库时知识库自身仍可 `git init`，但主仓库无身份锚点可校验，因此不建立镜像）

只保留：单一知识库 + mtime 会话边界 + 完整条目生成。

# 分支归属与合并状态判定

知识库按分支分目录存放，会话按信号分段归属。这个文件回答三个问题：**这次运行该写哪个目录**、**某条会话的结论算不算当前事实**、**跨分支续写的会话怎么切分**。

## 目录

1. 归属分支推导
2. 首次运行的确认交互
3. 分段归属协议
4. 合并状态判定
5. 断言分类
6. 与 U.3 的衔接

## 写入归属判定

Worktree 的临时分支未必从主线分出，也未必合回主线。Git 拓扑只能给出候选，不能可靠区分 sibling branch，所以**已确认的分支登记表是写入归属的唯一权威源**。

⚠️ **主线分支名不是 `main`，要从元信息读。** 主线知识库就在 `<KB>` 根（没有 `main/` 子目录），而仓库的主线**分支**可能叫 `master`、`trunk` 或任何名字。首次生成时探测当前分支并写入 `<KB>/_meta.json`：

```json
{ "anchor_kind": "main", "branch": "<首次生成时的当前分支名>" }
```

`<KB>/_branches.json` 按原始分支名登记：

```json
{
  "feat/short": {
    "mode": "inherit",
    "parent_anchor": "master",
    "confirmed_at": "2026-08-13T10:00:00+08:00"
  },
  "feat/long": {
    "mode": "independent",
    "parent_anchor": "master",
    "slug": "feat-long",
    "confirmed_at": "2026-08-13T10:05:00+08:00"
  }
}
```

判定顺序：

1. detached HEAD → 只读，不写。
2. 当前分支等于 `<KB>/_meta.json.branch` → `<KBR>=<KB>`，允许写。
3. 登记为 `independent` → `<KBR>=<KB>/.branches/<slug>`，允许写；校验目录 `_meta.json.branch` 与原始分支一致。
4. 登记为 `inherit` → 读取 `parent_anchor` 的知识库，但 `write_allowed=false`。短期分支的知识由父分支后续 update 从会话打捞，不能把未合并事实直接写进父目录。
5. 登记为 `archived` → 历史目录只读；当前同名分支视为可能被复用，展示旧 archive 信息并要求重新确认，确认前不得写。重新登记时先把旧项的 `slug/archive_path/archived_at/outcome` 原样迁入该 branch 的 `archive_history[]`，再把顶层改为新的 `inherit` 或 `independent` 状态；旧 archive 目录仍保留并继续校验，不能覆盖或丢元数据。
6. 未登记的非主线分支 → 只计算候选并询问一次；确认前只读，不得写。

**任何地方都不要把字符串 `"main"` 当分支名用。**

### 未登记分支的候选提示

写入父锚点只允许主线 `_meta.json.branch`。独立分支可以作为拓扑背景显示，但不能成为另一独立分支的 `parent_anchor`；否则需要链式知识合并，而本协议只定义“独立分支 → 主线”的一次合并。优先判断主线 tip 是否为 HEAD 祖先并报告距 HEAD 的提交数；不是祖先时可展示共同祖先，但必须明确它可能是 sibling，不能自动采用。

```bash
current=$(git branch --show-current)
# cand 只取主线 _meta.json 记录的原始分支名，不是目录名
for cand in "${candidates[@]}"; do
  if git merge-base --is-ancestor "$cand" HEAD; then
    git rev-list --count "$cand"..HEAD
  else
    base=$(git merge-base "$cand" HEAD) || continue
    echo "$cand 仅共享共同祖先 $base，不是 HEAD 的祖先"
  fi
done
```

主线分支已改名/删除、所有候选历史无关，或登记表与目录 `_meta.json` 冲突时，停止并询问，不要猜。

fallback 读取时（当前分支没有独立目录，读的是主线那份）必须**明确标注"这是主线知识库，不含本分支改动"**。让 AI 知道自己读的是主线视角，比让它以为读到了当前分支的知识安全得多。

## 首次运行的确认交互

首次在某个未登记非主线分支上运行时，展示候选和证据并询问一次：

```
当前分支 feat/big-sub 没有独立知识库。
候选父锚点：trunk（它是 HEAD 的祖先，距 HEAD 3 个 commit）
选择：[继承并保持只读 / 建独立目录 / 取消]
```

**把证据摆出来让用户确认，不要自动采用“距离最小”。** 选择记入 `_branches.json`，之后同一分支不再询问；已归档的同名分支例外，必须显式确认这是继续旧工作还是 Git 名称复用，不能悄悄复活旧 KBR。`archive_history` 是 append-only；新 active slug 的唯一性只与其他 active independent 比较，但所有历史 `archive_path` 在整个 registry 中仍须全局唯一。

判据本身很实用：短期实验分支（存活几小时、跑完即合）选"否"，知识靠会话打捞在归属分支的 update 里消化；长功能分支选"是"，产出直接落在自己的目录里。**在短期 Worktree 里跑 codewise 本就罕见**——几小时的工作不会中途停下来生成知识库，所以这个交互极少触发。

建独立目录时必须在 KB 锁内一次完成：

1. 记录当前知识库 HEAD 为 `_meta.json.forked_from`，并记录原始分支名；
2. 把主线 `INDEX.md` 与六类条目完整复制为自包含快照，控制文件不复制；
3. `_sync.json.baseline_commit` 取已确认 `parent_anchor` 与当前代码 HEAD 的 `merge-base`，无法解析则置 `null`；`synced_at` 置 `null`，确保首次分支 update 不漏旧会话；
4. 随即按 Phase U 更新该快照。不要因为新目录最初没有 INDEX 而转入 Phase G 再生成一份不同结构。

这保证分支 INDEX 的相对链接可解析，Phase M 也能以 `forked_from` 做真正的条目级三方比较。

## 分段归属协议

会话会被续写（resume），一个会话文件可能跨越多个分支。实测 1076 个 Claude 会话中有 20 个跨分支，最多一个跨了 6 个分支——而跨分支的往往是长会话，正是知识密度最高的那批。**按会话级一刀切定性，丢掉的就是最有价值的部分。**

各来源的记录粒度不同，必须分别处理：

| 来源 | 字段 | 粒度 | 切分方式 |
|---|---|---|---|
| Claude Code | `gitBranch` | **每条 user/assistant 消息，覆盖率 100%** | 直接读每条消息的字段，消息级精确，零推断 |
| Codex | `payload.git.branch` / `commit_hash` | 每次启动/resume 记一次 | 按标记点行号切段，每段继承最近的前置标记 |
| 其他来源 | 通常无分支字段 | — | 整个会话归为 `unknown-branch`，走保守隔离 |

Codex 的段内如果用户在别的终端切了分支，不会有记录——**段内假定分支不变，置信度标低一档**。这无法从数据修复，如实标注，不要假装精确。

**判定的输入是信号级分支，不是会话级分支。** 每个 pitfall / decision 候选按它所在消息（或所在段）的分支归属，然后各自做合并状态判定。会话级的 `branch_state` 只作快速粗筛。

## 合并状态判定

不是一次定性，而是**证据升级链**——`merged-likely` 是中间状态，必经二次验证推向确定。

### merged-confirmed（强证据，任一成立）

1. 会话分支仍存在，且 `git merge-base --is-ancestor <branch> HEAD` 成立
2. merge commit 的 message 明确提到该分支名
3. **会话里记录的 commit hash 经验证后是当前 HEAD 的祖先**

第 3 条最硬，也不依赖 baseline：先把会话 hash 限定为完整 40/64 位十六进制，再用 `git rev-parse --verify --end-of-options '<hash>^{commit}'` 取得规范 full hash，最后检查它是否为 HEAD 祖先。`baseline..HEAD` 只限定本轮新增扫描，不限定“是否已经合并”；首次生成没有 baseline、已早于 baseline 的旧会话也能因此确认。Codex 会话有 `payload.git.commit_hash`（实测约 87% 覆盖），Claude Code 没有对应字段，需从 transcript 里的 commit 操作反推。**来源之间证据强度不对称，不要假设一视同仁。**

所有来自 transcript/JSON 的分支名在进入 Git 前先通过 `git check-ref-format --branch`，并只解析精确 `refs/heads/<branch>`；随后命令只消费已解析 full hash。校验失败就是未知证据，不得把原字符串当 revspec/option 继续执行。

### merged-likely（仅文件级命中，必须二次验证）

判据是"会话触碰过的文件在 `baseline..HEAD` 有改动 + 时间窗吻合"。**这个判据在热点文件上几乎必然假阳性**：会话在某分支改了 `src/foo.ts` 但改动被放弃，同时主线上另一次工作也改了同一文件，文件级判据就会命中。

二次验证：**会话里 Edit/Write 的 `new_string` 内容片段，是否出现在当前代码中。**

- 命中 → 升为 `merged-confirmed`
- 未命中 → 降为 `unmerged`

用内容比对而非符号名比对：符号名对"只改了函数内部逻辑"的会话无效，而 Edit 的产出内容是直接证据，几乎无假阳性。

### unmerged（拆两种，处理相反）

| 情况 | 判据 | 处理 |
|---|---|---|
| **还没合并** | 主仓库对应分支仍存在 | 隔离。避免知识超前于代码 |
| **永不合并** | 分支已删除，且未合并进任何有知识库的分支 | **采纳因果断言**，写进 `decisions/` 标为已否决方案，或 `pitfalls/` 标为此路不通 |

第二种必须采纳——"试过 X 方案，因为 Y 失败了"正是 codewise 最该抢救的知识。实验失败、分支扔掉，代码里什么都不剩，只有会话记得为什么。一刀切隔离会把它丢掉。

判据很硬：分支还在不在，不需要猜。

## 断言分类

同一条会话里的两类断言，采纳规则完全不同。混为一谈会把知识库的核心价值砍掉。

<!-- 这一条是整套判定的地基，不要简化 -->

| 类型 | 例子 | 采纳规则 |
|---|---|---|
| **状态断言** | "X 已经修好了"、"现在架构是 Y"、"这个函数返回 Z" | **必须由当前代码验证。** 会话说了不算 |
| **因果断言** | "根因是 Z"、"试过 A 方案因为 B 失败"、"选 C 不选 D 因为 E" | **代码里根本验证不了**，随合并状态采纳 |

因果断言是会话的唯一价值来源——代码改了什么 `git diff` 已经覆盖，而**为什么这么改、试过什么失败了、误判过什么，只有会话记得**。

这个分类还顺带解决一个边界：长功能分支上"第 2 天写的实现第 4 天被推翻"的情况——状态断言被代码验证挡住（那版实现确实不存在了），而"试过 X 因 Y 失败"作为因果断言被保留。

因果类条目写入时带 `confidence` 字段：`merged-confirmed` → `confirmed`；经二次验证但仍有疑虑的 → `medium`，并在更新报告里单列供用户确认。

## 与 U.3 的衔接

U.3 交叉验证时，以下组合的处理是硬约束：

- 信号归属分支 = 当前归属分支，且 `merged-confirmed` → 因果断言直接采纳；状态断言仍走代码验证
- 信号归属其他分支，`merged-confirmed` → 同上（已合并即当前事实）
- `merged-likely` 未通过二次验证 → 按 `unmerged` 处理
- `unmerged` + 分支仍存在 → 只作演变记录，不进当前状态条目
- `unmerged` + 分支已删 → 因果断言进 `decisions/`/`pitfalls/` 并标为已废弃方案
- `unknown-branch`（无分支证据 / stale Worktree / 非 Claude-Codex 来源）→ 默认隔离，除非当前代码或当前 HEAD 历史独立复核通过；**扫描时 Worktree 的分支不能替代会话历史分支**

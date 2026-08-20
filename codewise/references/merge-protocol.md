# 三方合并协议

长功能分支合并回主线时，把 `<KB>/.branches/<slug>/` 的知识并进主线（`<KB>` 根）。这是**语义三方合并**——git 只负责存历史，合并判断由 codewise 做。

## 目录

1. 为什么不是 rebuild
2. 三方合并的 base
3. 条目级合并规则
4. 跨路径语义去重
5. 归档
6. 触发方式

## 为什么不是 rebuild

一个 feature 分支通常只改动全部模块中的一小部分，其余条目两边完全相同——重扫它们是纯浪费。用分叉点做基准的三方合并，**AI 参与面积 = 真冲突条目数**，通常是个位数，比"丢弃状态类条目重新生成"便宜一个数量级。

前提是有 base 版本。完整分支快照的 `_meta.json` 里显式记录了 `forked_from`，所以 base 总是可得——这比 `merge-base` 更可靠：分叉点是**记录的**而不是**推导的**，不会被 rebase 打乱，也不存在“找不到共同祖先”的边界情况。

## 三方合并的 base

```bash
# 分支目录 _meta.json 里的 forked_from 是知识库仓库当时的 HEAD
git -C <KB> show <forked_from>:<条目路径>
```

取不到（条目在 fork 之后才由主线新增）→ base 视为"不存在"，走下表的"两边都新增"。

## 条目级合并规则

只处理以下六类白名单里的 `.md` 条目：

```text
domains/**/*.md  shared/**/*.md  decisions/**/*.md
integrations/**/*.md  workflows/**/*.md  pitfalls/**/*.md
```

控制文件永不作为条目复制或三方合并到根：`_meta.json`、`_sync.json`、`_deleted`、`INDEX.md`、`.lock*`、`.gitignore`、`_identity.json`、`_branches.json`、`_dedup_pending.md`、`.branches/`、`.archive/`。

开始任何写入前运行只读清单校验器，并且后续只消费其 `entries` / `deletions`：

```bash
python3 <SKILL_DIR>/scripts/branch_manifest.py '<KB>/.branches/<slug>' \
  --kb '<KB>' --pretty
```

返回非零时整次 Phase M 停止。不能在校验后重新自行遍历目录，否则会重新把控制文件或越界路径带回输入。

对白名单里的每个分支条目执行：

| base | main 侧 | 分支快照侧 | 处理 | 需要 AI |
|---|---|---|---|---|
| 有 | 等于 base | 不等于 base | 直接采纳分支版本 | 否 |
| 有 | 不等于 base | 等于 base | 保持主线 | 否 |
| 有 | 不等于 base | 不等于 base | **语义融合**：能合就合；矛盾则以当前代码裁决状态断言、保留双方因果断言 | 是 |
| 有 | 任意 | 文件缺失且列于 `_deleted` | 先由当前代码验证删除，再删除或保留 | 可能 |
| 有 | 任意 | 文件缺失且未列于 `_deleted` | 快照损坏，停止 | — |
| 无 | 有同名 | 有同名 | 两边独立新增 → 送语义去重判断 | 是 |
| 无 | 无 | 有 | 直接采纳 | 否 |
| 无 | 有 | 无 | 保持主线新增 | 否 |

`_deleted` 由清单校验器整体读取为集合。每一行必须是上述六类目录下的相对 `.md` 路径；出现绝对路径、`..`、控制字符或白名单外路径时，整次合并停止且不执行部分删除。合法条目还要确认删除理由：误判可删；模块在分支移除属于状态断言，须由合并后的当前代码验证。

分支 `_meta.json`、`_sync.json` 只随源目录归档；分支 `INDEX.md` 丢弃，根 INDEX 最后重建。根 `_sync.json.baseline_commit` 在 Phase M **不前移**，除非同次完成了完整 Phase U；否则会跳过主线上尚未吸收的代码变化。合并后提示再跑 `$codewise update`。

因果类条目（`pitfalls/`、`decisions/`）的合并语义是**并集**：一个坑不会因为另一条分支上没记录就不成立。只有状态类条目（`domains/`、`shared/`、`integrations/`）才可能真冲突。

## 跨路径语义去重

按路径做三方比对看不出这种重复：

```
pitfalls/tauri-ipc-timing.md            ← 主线
.branches/feat-x/pitfalls/ipc-race.md   ← 分支     ← 同一个坑，不同文件名
```

合并后必须补一道扫描，但**不能是 N² 全库两两比对**：

1. **范围**：只扫本次合并新引入的条目，不扫全库
2. **粗筛**：对每个新条目，在主线的同类目录里找候选，命中任一条才进下一步——
   - 条目提到的文件路径有重叠
   - 条目提到的函数/类型标识符有重叠
   - 标题关键词有重叠
3. **AI 判断**：粗筛后通常只剩几对，逐对判断是否同一件事
4. **保守裁决**：判定为重复也**不自动合并**，两条都保留，写进 `<KB>/_dedup_pending.md`

第 4 条是刻意的：**丢信息不可逆，条目重复可逆。** `_dedup_pending.md` 持久化在知识库里，用户随时可以处理；已被 dismiss 的对下次不再报告，所以清单会收敛而不是滚雪球。

## 归档

代码是否已合并优先用分支 `<KBR>/_sync.json.baseline_commit` 判断：先由 control-state helper 校验并归一为 full hash，再执行 `git merge-base --is-ancestor <branch-baseline> HEAD`。这样 branch ref 已删除时仍可验证；baseline 缺失或不是祖先则停止。

合并 commit 前，在同一 KB 锁和同一 commit 中完成两个动作：把整个 `<KBR>` 移入一个尚不存在的 `<KB>/.archive/<slug>-<时间戳>/`，并把根 `_branches.json` 对应项改为 `mode:"archived"`，记录原 `slug`、唯一 `archive_path`、`archived_at` 与合并结果；已有 `archive_history` 原样保留。禁止覆盖既有 archive；归档目录 `_meta.branch` 必须继续匹配原始分支名，且归档后 `.branches/<slug>` 必须不存在。分支控制文件随归档保留。后续解析到 archived 的同名 Git 分支只能只读并要求重新确认；重新登记时把这次顶层 archive 记录迁入 append-only `archive_history`，不能继续写已移动路径或丢旧记录。

`.archive/` **不参与任何扫描**——INDEX 生成、条目统计、互链检查都走白名单（只看主线的六个分类目录和当前分支目录）。归档内容被当成当前知识是个静默错误，白名单从结构上排除了它。

分支被废弃而非合并时同样归档，但要在 `_meta.json` 标 `outcome: abandoned`；其中的因果断言按 [branch-resolution.md](branch-resolution.md) 的 `unmerged` + 分支已删规则处理——**"试过 X 因 Y 失败"要进主线 `decisions/` 标 `status: rejected`**，不能随目录一起归档了事。

## 触发方式

**只有显式触发一条路**：`$codewise merge <branch>`（或在 Skill picker 选中 Codewise 后输入 `merge <branch>`）。纯文本 `codewise` 与 `/codewise` 在禁用隐式调用时不会加载 Skill，不要把它们写成可用入口。

**普通 `$codewise update` 不会自动并入分支知识库** —— Phase U 的流程里没有扫描 `.branches/` 的步骤，分支目录会一直留在那里直到你显式合并。不要指望“跑一次 update 就自动收进来”。

代价是要记得做；换来的是合并时机完全可控——三方合并会改写主线条目，不该在用户没预期时发生。

**提示机制**：`update` 时若发现 `.branches/<slug>/` 对应的分支在主仓库已合并（`git merge-base --is-ancestor <branch> HEAD` 成立），在报告末尾提示一行「分支 X 已合并，其知识库尚未并入，可运行 `$codewise merge X`」——只提示，不自动执行。

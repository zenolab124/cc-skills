---
name: codewise
description: 显式调用 $codewise 时，按任务范围维护项目决策、契约与故障经验；完整历史扫描需明确要求。普通开发或仅讨论 Codewise 不触发。
---

# Codewise 项目知识维护

优先保留无法从当前代码轻易恢复的知识：决策原因、真实失败经验、跨端契约和有证据的验收边界。默认更新相关权威文档，不生成代码说明的第二份副本。

## 选择工作范围

| 用户请求 | 行为与按需读取 |
|---|---|
| 无参数 / `update` / 更新本次知识 | 按当前任务、指定路径或明确提交范围局部维护；读 [scoped-update.md](references/scoped-update.md) |
| 查找知识 / 解释历史原因 | 只读搜索相关源码、权威文档与旧知识条目；不更新同步状态 |
| `full-update` / 明确完整扫描代码与候选会话 | 读 [full-maintenance.md](references/full-maintenance.md)，保留旧独立知识库的完整维护流程 |
| `rebuild` / `refresh-docs` / `refresh-interfaces` / `merge <branch>` / `reidentify` | 显式高级操作，读 [full-maintenance.md](references/full-maintenance.md) 并只加载对应模式所需参考 |

这些是技能请求模式，不是独立 Shell 命令。`--root <path>` 指定项目 scope，未给时使用当前项目；`merge` 后的位置参数是分支名。不要把普通“更新”升级成完整扫描，也不要因旧库缺失、归档报错或未提供另一台机器会话而自动重建。

## 共同边界

- 遵守项目的平台分工、跨端确认、数据和部署权限。知识维护不授予客户端改造、云端写入或远程操作权限。
- 用户当前明确决定和项目权威文档表达意图；当前源码证明实现；历史会话与生成知识只补充原因。发生冲突时说明差异，不用历史摘要覆盖新决定。
- 先查相关人写文档，有合适位置就补充原文；没有可复用的新知识时允许零改动。不要为完成一次调用而凑条目、建新分类或堆接口数量。
- 普通更新不扫描全部会话，不强制多 Agent，不 bootstrap/fetch/push 旧知识库，不修改其任何文件或全仓 baseline/synced_at。
- 新知识只记录脱敏结论、相关文件和必要版本；不复制原始会话、私有标识或凭据。推断明确标为未验证，代码落地、提交、部署、发布与真机验收分别表述。

## 启动入口

`scripts/session_start.py` 只输出简短路径提示，不读取或注入 INDEX/文档正文，不代表已核验知识时效。`scripts/codewise-inject.sh` 是 Claude/Codex 共用的 Shell 适配器；本机 hooks 使用同一份内容，业务逻辑只在 Python 文件维护。Windows 可直接用 Python 调用同一入口，具体宿主注册与实机生效需在 Windows 核验。

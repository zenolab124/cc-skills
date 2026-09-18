# cc-skills

统一管理自建 Agent skills 及其配套规则的仓库。每个子目录即一套能力；Skill 按需安装到 Claude/Codex 的技能目录，配套规则通过各自安装工具管理。

## 仓库结构

```
cc-skills/
├── <skill-name>/
│   ├── SKILL.md          # 必需，含 frontmatter (name/description)
│   └── ...               # 脚本、模板、资源
└── CLAUDE.md
```

当前 skills：adb-visual-automation、blog、cross-talk、docflow、daily-summary、draw、fix-thinking、prd、recall、wechat-notify、windows-remote。

已退役：`codewise/` 保留为源码档案（2026-09-19）。本机已解除技能安装链接及 Claude/Codex SessionStart 注册；常规安装/同步不要重新链接或注册它。各项目使用源码与相关权威文档，原独立知识库保留为只读历史档案。其他机器的入口需在对应机器单独核验，不能从本机退役推断已同步。

特殊：fix-thinking 除手动触发外，还以 SessionStart hook 形式注册在 `~/.claude/settings.json`（指向 `~/.claude/skills/fix-thinking/check-thinking-patch.sh`），自动检测 VS Code 扩展更新清掉的思考块 patch。

本仓库只管理自建 skill。第三方 skill（如 cartographer、ui-styling 等）直接放在 `~/.claude/skills/` 下，不纳入版本控制。

## 同步机制

**单一真实源**：本仓库是唯一源，`~/.claude/skills/<name>` 是指向本仓库的符号链接。

Skill 链接指向本仓库；修改后由客户端重新发现/加载，当前会话已加载内容不保证即时刷新。配套 Rules 的安装副本须使用该能力的安装工具更新并检查，不能从 Git 拉取成功推断已生效。

### 文档协作 · DocFlow

- 唯一维护目录：`docflow/`；行为规则 `RULES.md`、整理流程 `SKILL.md`、中文模板和检查工具一起版本管理。
- 本机 Claude/Codex 接入：`python3 docflow/scripts/install.py install --clients both`；`diff` 预览、`check` 核对、`uninstall` 解除。
- 全局配置只管理 DocFlow 标记区块，正文来源唯一；禁止手动维护安装副本。Skill 以链接安装，不覆盖异源安装。
- 各项目保留自己的功能文档及必要导航；本仓库不收集业务知识、私人会话或生产状态。
- 使用方式和安装边界见 [DocFlow](docflow/SKILL.md)。源码拉取、安装一致、新会话加载、另一台机器验收分别确认。

### 新增 skill

1. 在本仓库创建 `<skill-name>/SKILL.md`（frontmatter 必须含 `name` 和 `description`）
2. 创建软链：`ln -s /Users/xt/workspace/cc-skills/<skill-name> ~/.claude/skills/<skill-name>`
3. 提交 git

### 删除 skill

1. `rm ~/.claude/skills/<skill-name>`（只删软链）
2. `git rm -r <skill-name>`

## 开发规范

- **SKILL.md frontmatter**：`name` 与目录名一致；`description` 要写清触发条件（"Use when..."）让 Claude 能正确匹配
- **脚本资源**：放在 skill 目录内，用相对路径引用
- **不要**在 `~/.claude/skills/` 下直接编辑（那是软链，等价于编辑本仓库，但容易混淆来源）
- **不要**提交 `.DS_Store`、临时文件

## 检查同步状态

```bash
ls -la ~/.claude/skills/   # 全部应为指向 cc-skills 的 symlink
```

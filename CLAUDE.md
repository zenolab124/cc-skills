# cc-skills

统一管理自建 Claude Code skills 的仓库。每个子目录即一个 skill，通过软链接同步到 `~/.claude/skills/`。

## 仓库结构

```
cc-skills/
├── <skill-name>/
│   ├── SKILL.md          # 必需，含 frontmatter (name/description)
│   └── ...               # 脚本、模板、资源
└── CLAUDE.md
```

当前 skills：blog、codewise。

本仓库只管理自建 skill。第三方 skill（如 cartographer、ui-styling 等）直接放在 `~/.claude/skills/` 下，不纳入版本控制。

## 同步机制

**单一真实源**：本仓库是唯一源，`~/.claude/skills/<name>` 是指向本仓库的符号链接。

在本仓库编辑 = Claude Code 立即生效，无需额外同步步骤。

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

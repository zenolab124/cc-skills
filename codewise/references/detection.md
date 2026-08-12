# 代码树检测与扫描范围推断

Phase 1.0 的完整规则。**主流程必须真做这步**——把“找代码树”从“子代理凭印象扫描”变成“主流程机械检测”，否则多代码树项目会被漏掉整棵树。


**主流程必须真做这步——把"找代码树"从"子代理凭印象扫描"变成"主流程机械检测"。**

### Step 1: 检测代码树根目录

主流程运行 shell 命令检测 `<ROOT>` 内可能存在的多个代码树:

```bash
# 主代码树
ls -d <ROOT>/src 2>/dev/null
ls -d <ROOT>/lib 2>/dev/null
ls -d <ROOT>/app 2>/dev/null

# 后端/桌面/云函数代码树(常见于全栈项目)
ls -d <ROOT>/src-tauri 2>/dev/null              # Tauri 桌面端 Rust
ls -d <ROOT>/uniCloud-* 2>/dev/null             # uni-app 云函数
ls -d <ROOT>/cloudfunctions 2>/dev/null         # 微信云开发
ls -d <ROOT>/functions 2>/dev/null              # Firebase / Vercel
ls -d <ROOT>/server <ROOT>/backend <ROOT>/api 2>/dev/null  # 通用后端

# Monorepo 子包(注意:遇到这种情况优先建议用户给具体子包路径)
ls -d <ROOT>/packages/*/src 2>/dev/null
ls -d <ROOT>/apps/*/src 2>/dev/null
```

存在的目录都是**有效代码树**。所有树都要被覆盖,**不能假设单一 src 入口**。

### Step 2: monorepo 检测

```bash
ls <ROOT>/pnpm-workspace.yaml <ROOT>/lerna.json 2>/dev/null
grep -l '"workspaces"' <ROOT>/package.json 2>/dev/null
```

如果检测到 monorepo + `<ROOT>` 是仓库根,**停下提示用户**:

> "检测到 monorepo(pnpm-workspace.yaml / lerna.json / yarn workspaces)。建议给具体子包路径作为 `<ROOT>`(如 `apps/web` 或 `packages/core`),否则跨子包的 imports 路径风格不一致,扫描质量会下降。继续在仓库根跑请确认。"

等用户决定后再继续。

### Step 3: 从元文件推断扩展名清单

主流程读取 `<ROOT>` 内的项目元文件,**动态生成**该项目应扫描的扩展名:

```
读 package.json → 加 .js/.jsx/.mjs/.cjs (基础)
  存在 typescript 依赖 → 加 .ts/.tsx
  存在 vue 依赖 → 加 .vue
  存在 svelte → 加 .svelte
  存在 astro → 加 .astro
  存在 unbuild/uni-app/dcloudio → 加 .uvue (uni-app 特有)
  存在 tailwind/unocss/postcss → 加 .css/.scss/.less/.postcss

读 Cargo.toml → 加 .rs
读 go.mod → 加 .go
读 pyproject.toml / setup.py / requirements.txt → 加 .py / .pyi
读 Podfile / Package.swift → 加 .swift / .m / .mm / .h
读 build.gradle / pom.xml → 加 .kt / .java
读 Gemfile → 加 .rb
读 composer.json → 加 .php
读 mix.exs → 加 .ex / .exs

不存在元文件(纯脚本/教学项目):
  抽样 <ROOT> 首层文件的扩展名分布,出现 ≥3 次的加入候选
  
用户在参数中明确指定的扩展名:无条件加入(覆盖优先级最高)
```

### Step 4: 推断排除规则

```
通用排除: .git、.svn、.hg、.idea、.vscode、coverage、tmp、.cache、*.min.js、*.generated.*、*.gen.*、*-lock.json
JS/TS: node_modules、.next、.nuxt、.turbo、dist、build、out、.vercel、.netlify
Rust: target
Python: __pycache__、.venv、venv、.tox、dist、build、.eggs、*.egg-info
Go: vendor
Swift/iOS: Pods、DerivedData、.build
Java/Kotlin: target、build、out、.gradle
uni-app: uni_modules、unpackage
```

### Step 4.5: 接口契约抽取

**目的**:让 AI 一进项目就掌握"对外调用入口"的全局地图。云函数项目尤其需要——接口零散在多个 cloudfunctions/<name>/index.obj.js 里,不汇总 AI 找不全。

主流程跑机械抽取脚本(按项目类型,有就跑,无就跳过):

```bash
# Tauri commands
grep -rn '#\[tauri::command\]' <ROOT>/src-tauri/src/ 2>/dev/null

# 云函数(uni-app/微信云开发/Firebase 等)
ls -d <ROOT>/cloudfunctions/*/ <ROOT>/uniCloud-*/cloudfunctions/*/ <ROOT>/functions/*/ 2>/dev/null

# REST endpoints(NestJS 装饰器)
grep -rEn '@(Get|Post|Put|Delete|Patch)\(' <ROOT>/src/ 2>/dev/null

# Express/Koa endpoints
grep -rEn '(app|router)\.(get|post|put|delete|patch)\(' <ROOT>/src/ 2>/dev/null

# GraphQL/gRPC/OpenAPI 契约文件
find <ROOT> -name '*.proto' -o -name '*.graphql' -o -name 'schema.gql' \
  -o -name 'openapi.yaml' -o -name 'swagger.yaml' 2>/dev/null

# 数据库 schema(uniCloud / Prisma)
ls <ROOT>/uniCloud-*/database/*.schema.json <ROOT>/prisma/schema.prisma 2>/dev/null
```

**抽取结果汇总成"接口契约清单"**,作为 Phase 4 INDEX.md "## 接口契约速查"区的输入。

**只汇总三件事**(每个接口):
1. **接口名**(命令名/云函数名/endpoint 路径)
2. **一句话职责**(从代码注释或函数名推断)
3. **入口位置**(代码文件路径)

**不抄完整签名**——签名跟代码同步无法保证(每次 update 间隙仍可能滞后),完整签名让 AI 跳代码看更可靠。

**判定边界**(什么算"对外调用入口"):
- ✅ **跨进程/网络的契约**:Tauri commands、Tauri events、云函数、HTTP endpoints、gRPC、GraphQL
- ✅ **跨语言边界**:Rust pub fn extern、Wasm exports
- ✅ **数据库 schema**(改 schema 破坏所有读写方,算契约)
- ❌ **内部 composable / utility / Pinia store actions**(本项目自己用,改了内部改一遍即可)
- ❌ **JQL 直接调数据库**(SDK 用法,不是命名接口;真正契约是数据库 schema)
- ❌ **组件 props/events**(内部组件不是对外接口,除非项目是 SDK/UI 库)
- ⚠️ **npm 包公开 API**(项目是库时算,是应用时不算)

### Step 4.6: 项目文档清单抽取 ⭐

**目的**:让 AI 一进项目就知道作者写过哪些权威文档(README / CLAUDE.md / docs/*.md / 设计文档等)。这些**人写文档比知识库自动条目更权威**——作者亲手写,跟代码一起更新。codewise 不能让它们对 AI 隐形。

主流程跑机械抽取,产出文档清单(只列路径,简介在 Phase 1.1 完整读后写):

```bash
# 根目录约定文档
ls <ROOT>/{README,CLAUDE,AGENTS,CONTRIBUTING,CHANGELOG,SECURITY}.md 2>/dev/null

# 项目文档目录
find <ROOT>/{docs,doc,design,specs,architecture} -maxdepth 3 -name '*.md' 2>/dev/null \
  | grep -v '/knowledge/' \
  | grep -v '/node_modules/'
```

**排除规则**:
- ❌ `<KB>/**`(自己生成,避免循环引用;它在 git-common-dir 下,不在工作树里)
- ❌ `<ROOT>/node_modules/**`(第三方)
- ❌ `<ROOT>/dist/`、`<ROOT>/build/`(构建产物)
- ❌ `<ROOT>/.git/`(版本控制元数据)

**清单格式**(供 Phase 1.1 完整读用):

```
[
  "README.md",
  "CLAUDE.md",
  "docs/CODEBASE_MAP.md",
  "docs/server-setup.md",
  "docs/recognition-api.md",
  ...
]
```

**LICENSE 等纯协议文档不抓**——对 AI 理解项目无价值。

### Step 5: 报告给用户

跑完后向用户报告检测结果:

```
检测结果:
- 有效代码树: [src/, src-tauri/, uniCloud-alipay/, ...]
- 推断扩展名: [.ts, .tsx, .vue, .rs, ...]
- 排除规则: [node_modules, target, ...]
- 监测到的元文件: package.json (deps: vue, typescript, dcloudio), Cargo.toml, ...
- 接口契约抽取: Tauri commands N 个 / 云函数 M 个 / REST endpoints P 个 / 契约文件 K 个
- 项目文档清单: N 个(README/CLAUDE/docs/*),将在 Phase 1.1 完整读后写简介

如有遗漏的代码树/扩展名/接口类型/文档,现在告知;否则继续 Phase 1.1。
```

**这些信息后面会写入**:
- `multi_codetree` 字段(代码树覆盖)
- INDEX.md "## 接口契约速查"区(接口清单)
- INDEX.md "## 项目文档导航"区(文档清单 + 简介)


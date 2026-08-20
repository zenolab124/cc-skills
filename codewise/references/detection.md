# 代码树检测与扫描范围推断

Phase 1.0 的完整规则。**主流程必须真做这步**——把“找代码树”从“子代理凭印象扫描”变成“主流程机械检测”，否则多代码树项目会被漏掉整棵树。

## 目录

1. 检测代码树与 monorepo
2. 推断扩展名与排除规则
3. 抽取接口契约与项目文档
4. 完整阅读文档与源码扫描
5. 局部刷新
6. 报告结果

## Step 1: 检测代码树根目录

主流程运行 shell 命令检测 `<ROOT>` 内可能存在的多个代码树:

```bash
# 主代码树、后端/桌面/云函数代码树
find "<ROOT>" -maxdepth 1 -type d \( \
  -name src -o -name lib -o -name app -o -name src-tauri \
  -o -name 'uniCloud-*' -o -name cloudfunctions -o -name functions \
  -o -name server -o -name backend -o -name api \
\) -print

# Monorepo 子包（遇到这种情况优先建议用户给具体子包路径）
for container in packages apps; do
  [ -d "<ROOT>/$container" ] || continue
  find "<ROOT>/$container" -mindepth 2 -maxdepth 2 -type d -name src -print
done
```

存在的目录都是**有效代码树**。所有树都要被覆盖，**不能假设单一 src 入口**。若没有命中任何约定目录，必须把 `<ROOT>` 本身作为 fallback 代码树；Go、Rust、Python 和纯脚本项目常把源码直接放在根目录。即使命中了子代码树，也要额外纳入 `<ROOT>` 第一层所有符合 Step 3 扩展名的 project-owned 入口文件（如 `main.go`、`manage.py`、`build.rs`），不能漏掉根级源码。

## Step 2: monorepo 检测

```bash
ls "<ROOT>/pnpm-workspace.yaml" "<ROOT>/lerna.json" 2>/dev/null
rg -l '"workspaces"' "<ROOT>/package.json" 2>/dev/null
```

如果检测到 monorepo + `<ROOT>` 是仓库根,**停下提示用户**:

> "检测到 monorepo(pnpm-workspace.yaml / lerna.json / yarn workspaces)。建议给具体子包路径作为 `<ROOT>`(如 `apps/web` 或 `packages/core`),否则跨子包的 imports 路径风格不一致,扫描质量会下降。继续在仓库根跑请确认。"

等用户决定后再继续。

## Step 3: 从元文件推断扩展名清单

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
读 CMakeLists.txt / meson.build / Makefile → 加 .c / .h / .cc / .cpp / .cxx / .hpp；脚本目标再加 .sh / .bash / .zsh
读 pubspec.yaml → 加 .dart
读 *.csproj / *.sln → 加 .cs

不存在元文件(纯脚本/教学项目):
  抽样 <ROOT> 首层文件的扩展名分布,出现 ≥3 次的加入候选
  
用户在参数中明确指定的扩展名:无条件加入(覆盖优先级最高)
```

元文件映射只给出初始清单，不能成为源码白名单。无论是否识别到元文件，都要在 Step 1 已选代码树内按排除规则机械统计扩展名分布，并纳入明显的 project-owned 源码扩展名；不能只抽样 `<ROOT>` 首层。尤其不得因没有映射而漏掉 C/C++、Dart、C#、shell、配置语言或项目自有 DSL。未知扩展先报告样本路径和数量，再由内容确认是否源码；二进制/生成物仍排除。

## Step 4: 推断排除规则

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

## Step 4.5: 接口契约抽取

**目的**:让 AI 一进项目就掌握"对外调用入口"的全局地图。云函数项目尤其需要——接口零散在多个 cloudfunctions/<name>/index.obj.js 里,不汇总 AI 找不全。

主流程跑机械抽取脚本(按项目类型,有就跑,无就跳过):

```bash
# Tauri commands
rg -n '#\[tauri::command\]' "<ROOT>/src-tauri/src" 2>/dev/null

# 云函数(uni-app/微信云开发/Firebase 等)
find "<ROOT>" -maxdepth 3 -type d \( -path '*/cloudfunctions/*' -o -path '*/functions/*' \) -print 2>/dev/null

# REST endpoints(NestJS 装饰器)
rg -n '@(Get|Post|Put|Delete|Patch)\(' "<ROOT>/src" 2>/dev/null

# Express/Koa endpoints
rg -n '(app|router)\.(get|post|put|delete|patch)\(' "<ROOT>/src" 2>/dev/null

# GraphQL/gRPC/OpenAPI 契约文件
rg --files "<ROOT>" -g '*.proto' -g '*.graphql' -g 'schema.gql' \
  -g 'openapi.yaml' -g 'swagger.yaml' 2>/dev/null

# 数据库 schema(uniCloud / Prisma)
rg --files "<ROOT>" -g 'uniCloud-*/database/*.schema.json' -g 'prisma/schema.prisma'
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

## Step 4.6: 项目文档清单抽取 ⭐

**目的**:让 AI 一进项目就知道作者写过哪些权威文档(README / CLAUDE.md / docs/*.md / 设计文档等)。这些**人写文档比知识库自动条目更权威**——作者亲手写,跟代码一起更新。codewise 不能让它们对 AI 隐形。

主流程跑机械抽取,产出文档清单(只列路径,简介在 Phase 1.1 完整读后写):

```bash
# 根目录约定文档
for name in README CLAUDE AGENTS CONTRIBUTING CHANGELOG SECURITY; do
  if [ -f "<ROOT>/$name.md" ]; then
    ls "<ROOT>/$name.md"
  fi
done

# 项目文档目录
for dir in docs doc design specs architecture; do
  [ -d "<ROOT>/$dir" ] || continue
  rg --files "<ROOT>/$dir" -g '*.md' -g '!**/knowledge/**' -g '!**/node_modules/**'
done
```

glob 只作加速；最终必须用 Phase 0 已解析的规范 `<KB>` 做路径组件级 containment 过滤，移除所有位于 `<KB>` 内的结果。不能用原始字符串前缀，也不能仅相信 glob，否则会把自身条目当项目文档写回 INDEX。

**排除规则**:
- ❌ 已解析的 `<KB>/**`（自己生成，避免循环引用；它位于主工作树但不受主仓库追踪）
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

## Step 5: 完整阅读与源码扫描

### 项目文档

完整读取 Step 4.6 清单里的每个文档，再写 50–100 字简介：说明主旨、关键内容/适用场景，必要时说明与其他文档的关系。不能只看标题或第一段。读文档获取意图，读代码验证事实；两者冲突时以代码为准，但保留文档中的设计原因。

优先识别并吸收已有 ASCII、Mermaid、dot 架构图和数据流描述到 `domains/architecture-overview.md`；没有现成架构图时再根据代码生成。

### 源码扫描

按 Step 1 的代码树（含无约定目录时的 `<ROOT>` fallback）、根级 project-owned 源文件和 Step 3 的扩展名分片。每棵代码树至少一个子代理；单树 50–200 个文件按目录拆成 2 组，超过 200 个拆成至少 3 组，每组不超过 100 个文件。每组报告：文件职责、依赖关系、公共类型/函数、功能域、第三方依赖、隐藏设计模式，以及主题/暗色模式/视觉约束。严格排除 vendor、生成物、已解析 `<KB>` 和 `<ROOT>` 外文件。

首次生成的精读阶段，每组必须完整读取负责范围内的全部源码，回查条目规划，并扫描生命周期副作用、动态加载、retry/fallback、HACK/FIXME/TODO/WORKAROUND 等隐藏逻辑。

## 局部刷新

- `refresh-docs`：重跑 Step 4.6，完整阅读文档，只替换 INDEX 的 `codewise-docs` 区。
- `refresh-interfaces`：重跑 Step 4.5，只替换 `codewise-interfaces` 区。

两种 refresh 都执行主流程相同的 clean source + snapshot 前后复核；不吸收未提交改动，不修改条目、`baseline_commit` 或 `synced_at`，也不替代完整 update。

## Step 6: 报告给用户

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

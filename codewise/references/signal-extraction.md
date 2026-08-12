# pitfalls 与 decisions 的信号识别

Phase 3.6 / 3.7 的完整扫描清单与判定规则。这两类条目承载的是**代码里看不出来的信息**（根因、误判过程、被否决的方案），是知识库最主要的价值来源，因此宁可多写也不要漏。


pitfalls 在大项目下容易偏保守(信号类型不全)。下面**扩展扫描信号清单 + 加交叉验证机制**——确保会话里讨论过的真实 bug 不会从 pitfalls 里漏掉。

### 3.6.1 扫描信号清单(在 3.2 子代理任务中并发扫描)

**注释类**:
- `// HACK`、`// FIXME`、`// WORKAROUND`、`// XXX` — 显式标记
- `// NOTE: ...` 后跟超过 30 字的描述 — 通常是非显而易见的约束
- `// TODO:` **如果说的是"防御代码/兼容性"**(例如"// TODO: remove once X is fixed")— 是真坑

**类型与编译绕行**:
- `as any` / `as unknown as X` / `// @ts-ignore` / `// @ts-expect-error` / `// eslint-disable-line` — 类型系统绕行处往往有故事
- Rust 的 `unsafe {}` 块、`unwrap_or_default` 在不该用的地方
- `# type: ignore` (Python) / `nolint` (Go)

**异常处理特殊逻辑**:
- retry 循环、exponential backoff
- catch 后的 fallback / 降级 / 静默 swallow
- 特定 error code 的特殊处理(NS error code、HTTP status 特判)
- `defer` / `finally` 块里的清理代码(顺序敏感)

**时序与并发**:
- `setTimeout(..., 0)`、`requestAnimationFrame` 的双重嵌套(常见为修 layout 时机)
- 不寻常的 `sleep` / `delay` 数字(`sleep(50)` 这种特定值往往有故事)
- `Promise.race` / `AbortController` 的边界处理
- 锁、信号量、原子操作

**平台与版本兼容**:
- `#ifdef` / `#cfg!()` / `process.platform` / `navigator.userAgent` 判断
- 浏览器特定 hack(Safari/iOS/IE)
- 多端编译指令(`#ifdef MP-WEIXIN` 等 uni-app 特有)

**数值与边界**:
- 浮点比较容差(`abs(a - b) < 1e-9`)
- 魔法常数(`8192`、`60000`、`0xFF` 这种)
- 数组/字符串边界处理(off-by-one 修复痕迹)

**序列化与反序列化**:
- `serde(rename = "...")` / `@SerializedName` 修正字段名
- TypeScript union 与后端 enum 的对齐(tagged union)
- JSON 字段大小写转换(camelCase ↔ snake_case)
- Pinia/Vuex 持久化对 Set/Map 的特殊处理

**资源管理**:
- localStorage/IndexedDB 配额检测和降级
- 内存释放、对象池、循环引用拆解
- 事件监听器的 add/remove 配对

**框架特性陷阱**:
- Vue 响应式对 `Map`/`Set`/`Date`/`Object.assign` 的特殊行为
- React `useEffect` 依赖项遗漏的注释痕迹
- Tauri/Electron 的进程间通信限制(payload 大小、序列化限制)

### 3.6.2 交叉验证

**Phase 3.3 的会话扫描结束后,做交叉验证**,确保会话里讨论过的 bug 不会被漏到 pitfalls:

```
信号源汇总:
  · 代码扫描 pitfalls 候选(3.6.1 各类信号) → 集合 A
  · 会话扫描 pitfalls 候选(Phase 3.3 子代理输出) → 集合 B

交叉验证规则:
  · 在 A 也在 B(代码有 hack 注释 + 会话讨论过) → 高置信度,必入 pitfalls
  · 仅在 A(代码有 hack 但会话没提) → 写入 pitfalls,但 note 标"代码痕迹推断"
  · 仅在 B(会话讨论但代码看不出) → 写入 pitfalls,但必须验证"代码确实是这么修的",不准凭印象
  · 在 B 但代码已不存在(被重构掉) → 走 decisions/ 演变记录,不进 pitfalls

零产出报警:
  会话里出现过 bug 修复 / 调试讨论 / 排除假设 → 但 pitfalls 候选数 = 0 → **异常,重新扫一遍 3.6.1 信号 + 重新读会话**
  这种"空手而归"在大型项目几乎一定是漏了
```

### 3.6.3 跳过该分类的合理条件

只有**同时满足**下面两条才能跳过 `pitfalls/`:
1. 3.6.1 各类信号在代码里**全都没命中**(不是"主要的没命中",是"全都没命中")
2. 会话扫描产出了**零 pitfalls 候选**

否则**必须产出至少一个 pitfalls 条目**。原版那种"没找到就跳过"在大项目实测中证明不靠谱——LLM 倾向于"觉得没什么大坑"而过度跳过。

### 3.6.4 pitfalls 条目结构(尽量包含 What/Why/Action)

每个 pitfalls 条目尽量包含:
- **What**: 现象/触发条件(代码里能看到的部分)
- **Why**: 根因(代码里看不出的部分,这是核心)
- **Action**: 怎么避免/修复(具体的代码姿势)

如果某条目暂时缺一两项,可以先写出来,标注"待补",后续追问用户补齐。**不要因为信息不全就跳过**——有半个总比没有强。

### 3.6.5 独立成条原则 ⭐(防打包合并)

**每个独立陷阱写成一个独立的 md 文件,不要把多个无关陷阱合并到同一文件**。

✅ 正确:
- `pitfalls/streaming-text-block-merging.md`(单一陷阱:流式 text 块合并)
- `pitfalls/serde-frontend-alignment.md`(单一陷阱:Rust enum 与 TS union 字段对齐)
- `pitfalls/decode-path-greedy.md`(单一陷阱:路径解码贪心匹配)

❌ 错误(打包合并,失去快速扫描能力):
- `pitfalls/rust-pitfalls.md`(把 8 个不相关的 Rust 陷阱压成一个文件)
- `pitfalls/vue-pitfalls.md`(把 9 个不相关的 Vue 陷阱压成一个文件)

**判定标准**:如果条目里出现了 ≥2 个**独立的根因或独立的修复姿势**,就该拆。同一根因的不同表现可以合并(比如"X 在场景 A 和场景 B 都会崩"是同一陷阱),不同根因的陷阱必须分开。

**这条规则比"信息量完整"更重要**——读者需要的是"快速定位 + 快速修复",不是"长篇综述"。打包条目违反知识库的核心使用模式。

## 3.7 主动识别 decisions

实测发现 decisions 在两个项目中都偏少(原版 cc-space-tauri 5 个、snap-ub 8 个)——LLM 倾向只把"显眼的选型"(用 Tauri 不用 Electron)写成 decision,漏掉**代码里隐式记录的权衡**。本节扩展信号清单。

### 3.7.1 扫描信号清单(在 3.2 子代理任务中并发扫描)

**注释类**:
- 代码注释里的 "chose X over Y because..."、"原本用 X,改成 Y"、"为什么不用 X"
- "Why ..." 块注释(常见于 Rust/Go 项目)
- 文件头的"## Design Notes"、"## Trade-offs"

**git 痕迹类**(grep 当前代码即可,不必读 git log):
- 文件名 `*-old.ts`、`*-deprecated.ts`、`Old*Adapter`(说明换过实现)
- import 注释掉的 `// import { X } from 'lib-old'`(候选库被否决的痕迹)
- package.json/Cargo.toml 中 deprecated 依赖的标记
- README/CHANGELOG 里的 "Migrated from X to Y" 段

**自研 vs 引入的权衡**:
- 项目内有自实现工具 + 同一目录有相关第三方库依赖(比如自实现 debounce 但又装了 lodash)
- 项目内有自实现 UI 组件,但 package.json 装了 wot-design-uni / element-plus(说明选择"自研 vs 第三方"的混用策略)

**性能/架构权衡**:
- 显式的"两级缓存"、"懒加载"、"sharding"等模式
- 异常的并行/串行选择(rayon 用在哪、为什么不用在别处)
- 数据库/存储的字段命名缩写(JSON 字段从 `userIdentifier` 缩成 `uid`,空间换性能)

**配置/常量类**:
- 多套配置文件并存(production/staging/dev 用不同策略)
- 魔法常数旁边的解释("180MB 阈值 = 实测 iPhone SE 内存上限")

### 3.7.2 与会话扫描交叉验证

跟 pitfalls 一样,Phase 3.3 会话扫描产出的 decisions 候选要跟代码扫描的交叉:

```
信号源汇总:
  · 代码扫描 decisions 候选(3.7.1) → 集合 A
  · 会话扫描 decisions 候选(Phase 3.3) → 集合 B

合并规则:
  · 在 A 也在 B(代码痕迹 + 会话讨论过) → 高置信度,必入 decisions
  · 仅在 A(代码有但会话没提) → 写入 decisions,需补充"为什么"段(读代码意图推断)
  · 仅在 B(会话讨论过但代码已不存在) → 走"决策演变记录":"曾经选过 X,后来换成 Y"
```

### 3.7.3 decisions 条目结构(尽量包含)

- **背景**:什么问题触发了这个决策
- **方案对比**:有哪些候选(A 是什么、B 是什么)
- **最终选择**:选了什么、关键原因
- **副作用**:这个选择带来了什么后续要注意的(可选)

如果某条目暂时缺方案对比(只知道"选了 X"但不知道"否决了 Y"),也可以先写,标注"待补"——不要因为信息不全就跳过。


---
name: adb-visual-automation
description: 为 ADB GUI 自动化提供安全的视觉定位、坐标缓存和点击后验状态机。Use when building or operating ADB automation that must avoid guessed coordinates, recover from UI drift, combine OpenCV with AI vision, or reuse verified taps across runs.
---

# ADB Visual Automation

通用引擎只负责确定性视觉定位和证据闭环；项目适配器负责页面语义、动作配置、模板、恢复策略和业务后验。禁止把应用包名、业务页面枚举或上传逻辑写入全局引擎。

## 固定状态机

1. 项目 Agent 基于 ADB 原始 PNG，从适配器允许的枚举中确认前置页面，并给出至少两条视觉证据。
2. `lookup` 查询完整环境签名和动作配置指纹对应的已验证坐标。
3. 缓存点击后重新截图；项目后验成功才调用 `feedback --source cache --result success`，失败一次立即停用。
4. 恢复前置页面并重新截图，调用 `match`。仅 `status=found` 可点击；低分或歧义结果不含坐标。
5. OpenCV 不可用时，项目 Agent 可输出配置白名单内的 `target`、归一化框和至少两条证据；必须经 `vision-target` 校验 ROI、面积、原图尺寸后才能获得设备坐标。
6. 每种来源点击后均执行同等严格的项目后验；只有成功才允许写缓存。

## 引擎边界

全局层提供：

- ADB 原始 PNG 到 Android 输入坐标的显式换算；
- 灰度与边缘融合的多尺度模板匹配、候选去重、阈值和歧义拒绝；
- AI 视觉语义白名单、归一化目标框、ROI、面积与证据数量校验；
- package、版本、原图/输入尺寸、方向、前置页面和完整动作配置指纹绑定；
- 文件锁、原子缓存、失败失效和结构化事件日志；
- 通用 QR 几何质量门。

项目层必须提供：

- `actions.json`、模板目录和运行状态目录；
- 页面枚举和每个动作的业务前置/后置条件；
- 安全恢复动作、点击/恢复/重启预算；
- 业务文件校验、上传、通知和应用收尾。

全局引擎不判断“图鉴”“登录成功”等业务状态，也不执行 ADB 点击。`Tapped` 只表示命令发出，不代表业务成功。

## 项目适配器结构

```text
<project>/.claude/automation/<adapter>/
├── actions.json
├── policy.md
├── run
├── scripts/
│   └── <business-validator>.py
├── templates/
└── tests/
```

`actions.json` 必须符合 `schemas/actions.schema.json`，并声明：

```text
schema_version = 1
engine_version = 1.0.0
```

引擎版本不完全一致时 `doctor` 直接返回 `incompatible_engine`；禁止静默迁移、降低阈值或继续使用旧缓存。

## 调用

优先通过项目适配器的 `run` 调用。直接调用全局入口时三个路径参数均为必填：

```bash
~/.claude/skills/adb-visual-automation/run \
  --actions <absolute-actions.json> \
  --templates-dir <absolute-templates-dir> \
  --state-dir <absolute-state-dir> \
  doctor
```

命令：`doctor`、`lookup`、`match`、`vision-target`、`feedback`、`verify-qrcode`。

## 安全约束

- 视觉定位只读取 `adb shell screencap -p` 拉取的原始 PNG；聊天/Monet 缩放预览只用于语义判断。
- 禁止预览像素换算、猜坐标、固定比例坐标兜底和 ROI 外点击。
- AI 不直接输出设备像素坐标，不修改动作阈值、ROI 或定位顺序。
- 任一来源失败后必须恢复已知前置页面并重新截图，不能在已变化页面套用下一来源。
- 运行预算由项目 policy 明确，禁止无界试点。
- 成功运行清理完整截图；失败证据限时、限权保留。日志不得包含凭据、二维码正文、聊天内容或账号数据。

## 新项目接入

1. 先定义页面枚举和每个动作的严格前置/后置证据，再采模板；不要从坐标倒推页面语义。
2. 为动作限定最小 ROI、语义 target、面积范围和点击偏移。
3. 用同页面正样本与相邻页面负样本校准阈值和最小候选分差。
4. 运行 `doctor`、通用测试和项目适配器测试。
5. 先监督冷路径，再监督缓存热路径；注入错误测试坐标确认一次后验失败即失效。
6. 出现第二个稳定消费项目后，再评估是否下沉 ADB MCP；在此之前保持 CLI 单一实现源。

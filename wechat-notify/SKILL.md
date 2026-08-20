---
name: wechat-notify
description: >
  通过微信机器人发送通知消息（OneBot API）。Use when user says "wechat-notify",
  "/wechat-notify", "通知一下", "发个微信", "群里说一下", "发通知", "微信通知",
  or after completing snap-import tasks and wanting to notify about updates.
---

# 微信通知 — OneBot API

通过本机 OneBot API 发送微信消息。

## 配置

| 项目 | 值 |
|------|---|
| OneBot API | `http://127.0.0.1:58080` |
| 默认目标（群聊） | `43626430695@chatroom` |
| 群聊接口 | `POST /send_group_msg` |
| 私聊接口 | `POST /send_private_msg`（备用，wxid `wxid_0137961370112`） |

## 发送方法

```bash
curl -s -X POST http://127.0.0.1:58080/send_group_msg \
  -H "Content-Type: application/json" \
  -d '{"message": [{"type": "text", "data": {"text": "<消息内容>"}}], "user_id": "43626430695@chatroom"}'
```

响应 `{"status":"ok"}` 即成功。

## 工作流程

1. **判断消息来源**：
   - 有 args → 直接作为消息文本发送
   - 无 args → 从当前会话上下文推断（刚完成的 snap-import 操作），按下方模板组装
2. **组装消息**：简洁、中文、适合微信阅读（手机屏幕宽度），用自然语言灵活措辞
3. **发送**：调 curl，确认 `status: ok`
4. **反馈**：告知用户"已发送"

## 消息风格

通知的核心是**数据校对报告**——拿真实数据比对数据库，告知校对结果。重点突出变更，不是简单列举。

### 礼包数据校对

```
📦 今日礼包校对完成

✅ 已校对：3 条数据一致
🔧 已修复 2 条：
  · 格温侍礼包 — 价格 598→648
  · 希娜礼包 — 天数 5→6、资源更新
🆕 新增 1 条：
  · ¥98 — 秘客
共处理 6 条
```

### OTA 平衡性调整

```
📋 平衡性调整已录入

· 卡牌A：能力 3→4
· 卡牌B：费用 2→3

详见小程序「OTA 历史」
```

### 新赛季卡牌

```
🃏 S{N} 新卡数据已更新

新卡：卡名1、卡名2、...
复刻：卡名3、卡名4（如有）

详见小程序「卡牌图鉴」
```

### 自由格式

args 非空时直接发送 args 文本，不套模板。

## 注意事项

- 当前阶段仅发私聊；切群时改 endpoint 为 `/send_group_msg`、`user_id` 改为群 ID（`xxx@chatroom`）
- OneBot 跑在本机，API 不可达时检查：`launchctl list | grep wechat-bot`
- 消息只支持纯文本和 base64 图片，不支持 markdown 渲染

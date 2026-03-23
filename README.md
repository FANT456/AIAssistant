# AIAssistant

本项目当前实现的是 **Phase 1：行程抽取与飞书入历**。

## 已实现

- 本地调用 Ollama (`/api/chat`)
- 支持直接输入行程文字
- 支持读取本地文本文件
- 输出标准日程 JSON
- 调用飞书 Calendar API 创建事件
- SQLite 持久化消息与处理记录
- 启动时检查 Ollama 服务与模型可用性

## 标准 JSON 结构

```json
{
  "title": "第一季度安全生产会议",
  "start_time": "2026-03-23 13:30",
  "end_time": "2026-03-23 14:30",
  "timezone": "Asia/Shanghai",
  "location": "总部3号会议室",
  "attendees": ["部门负责人", "质量专员"],
  "description": "第一季度安全生产会议"
}
```

## 环境要求

- Windows
- Python 3.10+
- 已安装 Ollama
- 已拉取模型：`qwen2.5:3b`
- 已创建飞书应用并拿到以下配置：
  - `FEISHU_APP_ID`
  - `FEISHU_APP_SECRET`
  - `FEISHU_USER_ACCESS_TOKEN` / `FEISHU_REFRESH_TOKEN` / `FEISHU_AUTH_CODE` 中至少一种

## 发布到 GitHub 前的安全原则

- 不要把真实的飞书密钥、日历 ID、数据库文件提交到仓库
- 不要提交本地 `.env`、`.db`、`.idea`、`__pycache__` 等文件
- 仓库中只保留 `.env.example` 这类占位模板
- 程序默认启用安全输出模式，会隐藏 `event_id`、`calendar_id` 和原始文本预览

## 安装依赖

```powershell
pip install -r requirements.txt
```

## 配置飞书环境变量

你可以参考仓库根目录下的 `.env.example`，再把真实值设置到本地环境变量中。

程序启动时会自动读取项目根目录 `.env`（如果存在），所以不需要每次手动输入环境变量。

推荐做法：

1. 复制 `.env.example` 为本地 `.env`
2. 在 `.env` 中填写真实值（不要提交到 GitHub）

```powershell
Copy-Item .\.env.example .\.env
```

```powershell
$env:FEISHU_APP_ID="cli_xxx"
$env:FEISHU_APP_SECRET="xxxx"
$env:FEISHU_USER_ACCESS_TOKEN="u-xxx"
$env:FEISHU_REFRESH_TOKEN="r-xxx"
$env:FEISHU_CALENDAR_ID=""
```

可选配置：

```powershell
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:MODEL_NAME="qwen2.5:3b"
$env:AI_ASSISTANT_DB_PATH=".\chat_history.db"
$env:AI_ASSISTANT_SAFE_LOGGING="1"
$env:FEISHU_TOKEN_REFRESH_SKEW_SECONDS="300"
# 默认本地存储位置：%LOCALAPPDATA%\AIAssistant\feishu_tokens.dat
$env:FEISHU_TOKEN_STORE_PATH=""
```

## 飞书 Token 自动续期与本地安全存储

当前程序已支持：

1. 优先读取本地安全保存的飞书 token 状态
2. 当 `user_access_token` 距离过期不足 `FEISHU_TOKEN_REFRESH_SKEW_SECONDS`（默认 300 秒）时，自动使用 `refresh_token` 刷新
3. 首次通过 `FEISHU_AUTH_CODE` 换取到的 `refresh_token` 会自动保存到本地
4. 刷新后的最新 `access_token / refresh_token / expires_at` 会覆盖保存，供下次启动继续使用

Windows 默认会把 token 保存在：

```text
%LOCALAPPDATA%\AIAssistant\feishu_tokens.dat
```

在 Windows 上，该文件会使用 DPAPI 绑定当前本机/当前用户进行加密，不会把明文 token 写回项目仓库。

推荐配置顺序：

- 最佳：`FEISHU_APP_ID` + `FEISHU_APP_SECRET` + `FEISHU_REFRESH_TOKEN`
- 首次授权：`FEISHU_APP_ID` + `FEISHU_APP_SECRET` + `FEISHU_AUTH_CODE`
- 兼容模式：仅 `FEISHU_USER_ACCESS_TOKEN`（可调用，但无法自动刷新）

## 启动 Ollama

```powershell
ollama serve
ollama pull qwen2.5:3b
```

## 运行程序

```powershell
python .\src\main.py
```

## 飞书 API 对齐说明（Calendar V4）

当前实现对齐了飞书创建日程接口：

- User Token: `POST /open-apis/authen/v2/oauth/token`
- 查询日历列表: `GET /open-apis/calendar/v4/calendars`
- 创建日程: `POST /open-apis/calendar/v4/calendars/:calendar_id/events`
- `Authorization: Bearer <user_access_token>`
- `Content-Type: application/json; charset=utf-8`
- `Accept: application/json`

当前程序的飞书流程为：

1. 优先读取本地安全保存的 token 状态
2. 若本地 `user_access_token` 即将过期，自动使用 `refresh_token` 续期
3. 如果本地无 token，但配置了 `FEISHU_REFRESH_TOKEN`，优先拉取一组新的 token 并保存到本地
4. 如果未提供 refresh token，则使用 `FEISHU_AUTH_CODE` 按 OAuth 文档换取 `user_access_token`
5. 如果配置了 `FEISHU_CALENDAR_ID`，优先使用该日历
6. 如果未配置 `FEISHU_CALENDAR_ID`，按官方接口分页查询日历列表（默认 `page_size=500`）
7. 从 `calendar_list` 中优先选择 `type=primary` 的日历；如无主日历，则回退到默认日历 / 第一个可写日历
8. 使用解析出的 `calendar_id` 创建日程；若请求时发现 token 已失效，会自动强制刷新并重试一次

创建日程请求体核心字段：

```json
{
  "summary": "会议标题",
  "description": "会议描述",
  "start_time": {
    "timestamp": "1763911200",
    "timezone": "Asia/Shanghai"
  },
  "end_time": {
    "timestamp": "1763914800",
    "timezone": "Asia/Shanghai"
  }
}
```

接口失败时会尽量返回：飞书 `code/msg`、HTTP 状态、`log_id`（如果飞书返回）。

### OAuth 授权码模式说明

如果你不直接提供 `FEISHU_USER_ACCESS_TOKEN`，则需要提供：

- 可选但推荐 `FEISHU_REFRESH_TOKEN`
- `FEISHU_AUTH_CODE`
- 可选 `FEISHU_REDIRECT_URI`
- 可选 `FEISHU_CODE_VERIFIER`

注意：

- 授权码有效期只有 5 分钟
- 授权码只能使用一次
- `redirect_uri` 必须与获取授权码时保持一致

## 使用方式

### 1. 直接粘贴行程文字

启动程序后，直接输入：

```text
公司定于2026年3月23日，下午13:30召开第一季度安全生产会议。地点在总部3号会议室，参会人员包括部门负责人和质量专员。
```

程序会：

1. 调用 Ollama 理解内容
2. 输出标准 JSON
3. 调用飞书日历 API 创建事件
4. 将处理结果写入 SQLite

### 2. 读取本地文件

```powershell
/file .\data\demo.txt
```

## 内置命令

- `/file <path>` 读取本地文件并处理
- `/records` 查看最近处理记录
- `/history` 查看历史记录
- `/count` 查看消息总数与处理记录总数
- `/models` 查看本地模型
- `/clear` 清空消息历史与处理记录
- `/quit` 退出程序

## 验证建议

1. 直接输入一段行程文字，确认终端打印标准 JSON。
2. 使用 `/file <path>` 读取 `.txt` 或 `.md` 文件，确认可正常抽取。
3. 配置好飞书环境变量后，确认创建成功提示出现。
4. 运行 `/records`，确认状态和错误信息被记录；默认安全模式下不会展示敏感内容。
5. 检查项目根目录下是否生成 `chat_history.db`。

## 测试

本地 mock 单测（不需要真实飞书密钥）：

```powershell
python -m unittest tests.test_feishu_client -v
```

本地 token 存储测试：

```powershell
python -m unittest tests.test_token_store -v
```

主流程集成测试（输入是一段自然语言，不是预先格式化后的 dict）：

```powershell
python -m unittest tests.test_main_flow -v
```

基础 smoke test：

```powershell
python .\tests\smoke_test.py
```

可选真实联调（需要本地已设置飞书环境变量）：

```powershell
python .\src\main.py
```

如果你不想手动填写 `FEISHU_CALENDAR_ID`，可以将其留空，让程序在启动或创建事件时自动查询日历列表并选择主日历。

## 隐私与安全输出

- 默认开启：`AI_ASSISTANT_SAFE_LOGGING=1`
- 开启后会隐藏：
  - 飞书 `calendar_id`
  - 飞书 `event_id`
  - `/history` 和 `/records` 中的原始文本预览
- 如果你在纯本地调试时想看完整内容，可手动关闭：

```powershell
$env:AI_ASSISTANT_SAFE_LOGGING="0"
```

## 下一步（暂不在本轮实现）

- `Phase 1.5`：多条日程拆分、创建前确认、失败重试
- `Phase 2`：规则校验与 Prompt 优化


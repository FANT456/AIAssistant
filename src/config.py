"""
AI Assistant 配置文件
"""

import os
from pathlib import Path


# 项目路径配置
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent


def _load_env_file(env_path: Path):
    """读取项目根目录 .env，不覆盖已存在的系统环境变量。"""
    if not env_path.exists() or not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()
            if "=" not in line:
                continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


_load_env_file(PROJECT_ROOT / ".env")

# Ollama 配置
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_API_CHAT = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_API_TAGS = f"{OLLAMA_BASE_URL}/api/tags"

# 模型配置（优先 3B 小模型，保证响应速度）
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:3b")

# 数据库配置
DB_PATH = os.getenv("AI_ASSISTANT_DB_PATH", str(PROJECT_ROOT / "chat_history.db"))

# 本地状态目录（Windows 默认使用 %LOCALAPPDATA%\AIAssistant）
LOCAL_STATE_DIR = Path(os.getenv("LOCALAPPDATA", str(PROJECT_ROOT))) / "AIAssistant"

# 飞书配置（建议通过环境变量注入）
FEISHU_BASE_URL = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn")
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_USER_ACCESS_TOKEN = os.getenv("FEISHU_USER_ACCESS_TOKEN", "")
FEISHU_REFRESH_TOKEN = os.getenv("FEISHU_REFRESH_TOKEN", "")
FEISHU_AUTH_CODE = os.getenv("FEISHU_AUTH_CODE", "")
FEISHU_REDIRECT_URI = os.getenv("FEISHU_REDIRECT_URI", "")
FEISHU_CODE_VERIFIER = os.getenv("FEISHU_CODE_VERIFIER", "")
FEISHU_CALENDAR_ID = os.getenv("FEISHU_CALENDAR_ID", "")
FEISHU_TOKEN_STORE_PATH = os.getenv("FEISHU_TOKEN_STORE_PATH", str(LOCAL_STATE_DIR / "feishu_tokens.dat"))
FEISHU_TOKEN_REFRESH_SKEW_SECONDS = int(os.getenv("FEISHU_TOKEN_REFRESH_SKEW_SECONDS", "300"))
FEISHU_API_USER_TOKEN = f"{FEISHU_BASE_URL}/open-apis/authen/v2/oauth/token"
FEISHU_API_CALENDARS = f"{FEISHU_BASE_URL}/open-apis/calendar/v4/calendars"
FEISHU_API_CALENDAR_EVENT = FEISHU_API_CALENDARS

# 日程默认配置
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai")
DEFAULT_EVENT_DURATION_MINUTES = int(os.getenv("DEFAULT_EVENT_DURATION_MINUTES", "60"))

# 安全输出配置：默认隐藏敏感字段和原始内容预览
SAFE_LOGGING = os.getenv("AI_ASSISTANT_SAFE_LOGGING", "1").strip().lower() not in {"0", "false", "no"}

# 系统提示词
SYSTEM_PROMPT = (
    "你是一个本地 AI 助手，负责与用户进行自然语言对话。"
    "请用简洁、准确、友好的方式回答用户的问题。"
    "如果不确定，请如实告知。"
)

SCHEDULE_EXTRACTION_PROMPT = """
你是一个日程信息抽取助手。你的任务是从用户提供的中文文本中提取日程信息，并严格输出一个 JSON 对象。

要求：
1. 只输出 JSON，不要输出 Markdown，不要输出解释。
2. JSON 字段固定为：
   title, start_time, end_time, timezone, location, attendees, description
3. 时间格式必须为 "YYYY-MM-DD HH:MM"。
4. timezone 默认输出 "Asia/Shanghai"。
5. attendees 必须是字符串数组。
6. 如果某个字段无法确定：
   - title 尽量根据会议主题提炼，实在无法判断时填 "未命名日程"
   - end_time 填 null
   - location 填空字符串
   - attendees 填空数组
   - description 尽量保留原始关键信息摘要
7. 如果文本里包含多个事件，只提取最核心的一条。
""".strip()

# 上下文窗口：最多携带多少轮历史对话发送给模型
MAX_CONTEXT_ROUNDS = 10

"""
LLM 客户端模块 - 与 Ollama 交互
支持流式输出、多轮对话上下文
"""

import json
import os
from datetime import datetime

import requests
# from config import (
#     OLLAMA_API_CHAT,
#     OLLAMA_API_TAGS,
#     MODEL_NAME,
#     SYSTEM_PROMPT,
#     SCHEDULE_EXTRACTION_PROMPT,
#     MAX_CONTEXT_ROUNDS,
# )


class LLMClient:
    """Ollama 大模型客户端"""

    def __init__(self):
        self.model = "qwen3:8b"
        self.OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.api_url = f"{self.OLLAMA_BASE_URL}/api/chat"
        self.last_request_failed = False
        self.last_error_message = ""
        self.SYSTEM_PROMPT= (
            "你是一个本地 AI 助手，负责与用户进行自然语言对话。"
            "请用简洁、准确、友好的方式回答用户的问题。"
            "如果不确定，请如实告知。"
        )
        self.MAX_CONTEXT_ROUNDS=10
        self.SCHEDULE_EXTRACTION_PROMPT= """
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

    def _mark_request_success(self):
        """标记最近一次请求成功"""
        self.last_request_failed = False
        self.last_error_message = ""

    def _mark_request_failure(self, message: str):
        """标记最近一次请求失败"""
        self.last_request_failed = True
        self.last_error_message = message

    def check_connection(self) -> bool:
        """检查 Ollama 服务是否可用"""
        try:
            resp = requests.get(f"{self.OLLAMA_BASE_URL}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    def get_available_models(self) -> list[str]:
        """获取 Ollama 已安装的模型列表"""
        try:
            resp = requests.get(f"{self.OLLAMA_BASE_URL}/api/tags", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except requests.ConnectionError:
            pass
        return []

    def _build_messages(self, user_input: str, history: list[dict]) -> list[dict]:
        """
        构建发送给模型的消息列表
        :param user_input: 当前用户输入
        :param history: 历史消息 [{"role": ..., "content": ...}, ...]
        :return: 完整消息列表（含 system prompt）
        """
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        # 添加历史上下文（最近 N 轮）
        context_messages = history[-(self.MAX_CONTEXT_ROUNDS * 2):]
        messages.extend(context_messages)

        # 添加当前用户输入
        messages.append({"role": "user", "content": user_input})

        return messages

    def _build_schedule_messages(self, source_text: str) -> list[dict]:
        """构建日程抽取请求消息"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        system_with_time = (
            f"当前时间是：{now_str}（Asia/Shanghai）。\n\n"
            + self.SCHEDULE_EXTRACTION_PROMPT
        )
        return [
            {"role": "system", "content": system_with_time},
            {
                "role": "user",
                "content": (
                    "请从下面的内容中提取一条日程信息，并输出标准 JSON：\n\n"
                    f"{source_text}"
                ),
            },
        ]

    def _post_chat(self, messages: list[dict], stream: bool):
        """统一的 Ollama Chat 请求"""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        return requests.post(self.api_url, json=payload, stream=stream, timeout=120)

    def chat_stream(self, user_input: str, history: list[dict] = None):
        """
        流式对话（生成器）
        :param user_input: 用户输入
        :param history: 历史消息列表
        :yields: 逐个 token 的文本片段
        """
        if history is None:
            history = []

        messages = self._build_messages(user_input, history)

        try:
            self._mark_request_success()
            resp = self._post_chat(messages, stream=True)
            resp.raise_for_status()

            for line in resp.iter_lines(decode_unicode=True):
                if line:
                    chunk = json.loads(line)
                    # Ollama 流式返回格式: {"message": {"content": "..."}, "done": false}
                    if "message" in chunk and "content" in chunk["message"]:
                        yield chunk["message"]["content"]
                    if chunk.get("done", False):
                        break

        except requests.ConnectionError:
            error_message = "[错误] 无法连接到 Ollama 服务，请确认 Ollama 已启动。"
            self._mark_request_failure(error_message)
            yield f"\n{error_message}"
        except requests.Timeout:
            error_message = "[错误] 请求超时，模型可能正在加载，请稍后重试。"
            self._mark_request_failure(error_message)
            yield f"\n{error_message}"
        except Exception as e:
            error_message = f"[错误] 请求失败: {e}"
            self._mark_request_failure(error_message)
            yield f"\n{error_message}"

    def chat(self, user_input: str, history: list[dict] = None) -> str:
        """
        非流式对话（一次性返回完整回复）
        :param user_input: 用户输入
        :param history: 历史消息列表
        :return: AI 完整回复文本
        """
        if history is None:
            history = []

        messages = self._build_messages(user_input, history)

        try:
            self._mark_request_success()
            resp = self._post_chat(messages, stream=False)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

        except requests.ConnectionError:
            error_message = "[错误] 无法连接到 Ollama 服务，请确认 Ollama 已启动。"
            self._mark_request_failure(error_message)
            return error_message
        except requests.Timeout:
            error_message = "[错误] 请求超时，模型可能正在加载，请稍后重试。"
            self._mark_request_failure(error_message)
            return error_message
        except Exception as e:
            error_message = f"[错误] 请求失败: {e}"
            self._mark_request_failure(error_message)
            return error_message

    def extract_schedule_json(self, source_text: str) -> str:
        """
        从自然语言文本中提取标准日程 JSON
        :param source_text: 原始日程文本
        :return: 模型返回的 JSON 文本或错误消息
        """
        messages = self._build_schedule_messages(source_text)

        try:
            self._mark_request_success()
            resp = self._post_chat(messages, stream=False)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

        except requests.ConnectionError:
            error_message = "[错误] 无法连接到 Ollama 服务，请确认 Ollama 已启动。"
            self._mark_request_failure(error_message)
            return error_message
        except requests.Timeout:
            error_message = "[错误] 请求超时，模型可能正在加载，请稍后重试。"
            self._mark_request_failure(error_message)
            return error_message
        except Exception as e:
            error_message = f"[错误] 日程抽取失败: {e}"
            self._mark_request_failure(error_message)
            return error_message

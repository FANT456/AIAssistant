"""
AI Assistant 主程序入口
Phase 1: 行程文本/文件 → JSON 抽取 → 飞书日历 → SQLite 存储
"""

import sys
from pathlib import Path

from config import MODEL_NAME, OLLAMA_BASE_URL, SAFE_LOGGING
from database import ChatDatabase
from feishu_client import FeishuCalendarClient
from json_utils import ScheduleJsonError, normalize_schedule_json, to_pretty_json
from llm_client import LLMClient


def print_banner():
    """打印启动横幅"""
    print("=" * 60)
    print("  📅 本地 AI 行程助手 (Phase 1)")
    print("  模型: " + MODEL_NAME)
    print("=" * 60)
    print()
    print("使用方式:")
    print("  1. 直接粘贴一段行程文字并回车")
    print("  2. 或使用 /file <文件路径> 读取本地文本文件")
    print()
    print("命令:")
    print("  /file <path>  读取本地文件并处理")
    print("  /records      查看最近处理记录")
    print("  /history      查看消息历史")
    print("  /count        查看消息数和处理记录数")
    print("  /models       查看可用模型")
    print("  /clear        清空历史与处理记录")
    print("  /quit         退出程序")
    print()


def summarize_text(text: str, preview_limit: int = 60) -> str:
    """根据安全模式返回可展示的文本摘要。"""
    compact = " ".join(text.split())
    if SAFE_LOGGING:
        return f"[内容已隐藏，长度 {len(text)} 字符]"
    return f"{compact[:preview_limit]}{'...' if len(compact) > preview_limit else ''}"


def summarize_identifier(value: str, label: str = "ID") -> str:
    """根据安全模式返回可展示的标识符。"""
    if not value:
        return ""
    if SAFE_LOGGING:
        return f"[{label} 已隐藏]"
    if len(value) <= 8:
        return value
    return value[:4] + "..." + value[-4:]


def startup_check(llm: LLMClient, feishu: FeishuCalendarClient) -> bool:
    """启动前检查依赖服务与关键配置"""
    print("[启动检查] 正在连接 Ollama 服务...")
    if not llm.check_connection():
        print("[错误] 无法连接到 Ollama 服务！")
        print("  请确认:")
        print("  1. Ollama 已安装并启动")
        if SAFE_LOGGING:
            print("  2. 服务运行在 OLLAMA_BASE_URL 指定的地址")
        else:
            print(f"  2. 服务运行在 {OLLAMA_BASE_URL}")
        print()
        print("  启动方式: 在终端运行 'ollama serve'")
        return False

    print("[启动检查] Ollama 服务已连接 ✓")

    # 检查模型是否可用
    models = llm.get_available_models()
    if not models:
        print(f"[警告] 未检测到任何模型，请先拉取模型:")
        print(f"  ollama pull {MODEL_NAME}")
        return False

    if not any(MODEL_NAME in m for m in models):
        print(f"[警告] 未检测到模型 '{MODEL_NAME}'")
        print(f"  可用模型: {', '.join(models)}")
        print(f"  请运行: ollama pull {MODEL_NAME}")
        print(f"  或修改 config.py 中的 MODEL_NAME")
        return False

    print(f"[启动检查] 模型 '{MODEL_NAME}' 已就绪 ✓")

    if feishu.has_valid_configuration():
        try:
            resolved_calendar_id = feishu.resolve_calendar_id()
        except Exception as exc:
            print("[启动检查] 飞书认证配置有效，但日历查询失败。")
            print(f"  原因: {exc}")
            print("  程序仍可运行 JSON 抽取，但写入飞书日历会失败。")
        else:
            if SAFE_LOGGING:
                print("[启动检查] 飞书凭证与日历解析已就绪 ✓")
            else:
                calendar_display = summarize_identifier(resolved_calendar_id, label="calendar_id")
                print(f"[启动检查] 飞书凭证与日历解析已就绪 ✓ (calendar_id={calendar_display})")
    elif feishu.is_configured():
        print("[启动检查] 飞书配置已检测到，但内容无效。")
        for issue in feishu.get_invalid_config_issues():
            print(f"  - {issue}")
        print("  请修正 .env 中的飞书配置后再重试写入日历。")
    else:
        missing = ", ".join(feishu.get_missing_config_fields())
        print("[启动检查] 飞书配置未完成。")
        print(f"  缺少: {missing}")
        print("  程序仍可运行 JSON 抽取，但写入飞书日历会失败。")

    return True


def read_text_file(file_path: str) -> tuple[str, str]:
    """读取本地文本文件内容，返回(文件名, 文件内容)。"""
    normalized = file_path.strip().strip('"').strip("'")
    if not normalized:
        raise ValueError("请提供文件路径，例如 /file .\\data\\demo.txt")

    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"目标不是文件: {path}")

    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return path.name, path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError("unknown", b"", 0, 1, f"无法识别文件编码: {path}")


def process_schedule_text(
    source_text: str,
    input_type: str,
    source_name: str,
    db: ChatDatabase,
    llm: LLMClient,
    feishu: FeishuCalendarClient,
):
    """处理一段日程文本：抽取 JSON，调用飞书，并保存记录。"""
    clean_text = source_text.strip()
    if not clean_text:
        print("[系统] 输入内容为空，已跳过。\n")
        return

    db.save_message("user", clean_text)

    print("[处理中] 正在调用模型抽取标准 JSON...")
    raw_output = llm.extract_schedule_json(clean_text)

    if raw_output and not llm.last_request_failed:
        db.save_message("assistant", raw_output)

    if llm.last_request_failed:
        print(raw_output + "\n")
        db.save_schedule_record(
            input_type=input_type,
            source_name=source_name,
            source_text=clean_text,
            extracted_json=raw_output,
            status="llm_failed",
            error_message=llm.last_error_message,
        )
        return

    try:
        schedule = normalize_schedule_json(raw_output, source_text=clean_text)
    except ScheduleJsonError as exc:
        print(f"[错误] 模型输出的 JSON 不合法: {exc}")
        if SAFE_LOGGING:
            print("[原始输出] 已隐藏（安全模式已开启）\n")
        else:
            print("[原始输出]")
            print(raw_output)
            print()
        db.save_schedule_record(
            input_type=input_type,
            source_name=source_name,
            source_text=clean_text,
            extracted_json=raw_output,
            status="json_invalid",
            error_message=str(exc),
        )
        return

    schedule_payload = {key: value for key, value in schedule.items() if key != "source_text"}

    if SAFE_LOGGING:
        print("[抽取结果] 标准 JSON 已生成（安全模式已隐藏具体内容）。")
        print("  字段: title, start_time, end_time, timezone, location, attendees, description")
        print()
    else:
        print("[抽取结果] 标准 JSON 如下:")
        print(to_pretty_json(schedule_payload))
        print()

    print("[处理中] 正在写入飞书日历...")
    create_result = feishu.create_event(schedule_payload)

    if create_result.success:
        print("[成功] 已写入飞书日历 ✓")
        if create_result.event_id:
            print(f"  event_id: {summarize_identifier(create_result.event_id, label='event_id')}")
        print()
        db.save_schedule_record(
            input_type=input_type,
            source_name=source_name,
            source_text=clean_text,
            extracted_json=schedule_payload,
            status="success",
            feishu_event_id=create_result.event_id,
        )
    else:
        print(f"[错误] {create_result.error_message}\n")
        db.save_schedule_record(
            input_type=input_type,
            source_name=source_name,
            source_text=clean_text,
            extracted_json=schedule_payload,
            status="feishu_failed",
            error_message=create_result.error_message,
        )


def print_message_history(db: ChatDatabase):
    """打印消息历史。"""
    messages = db.get_all_messages()
    if not messages:
        print("[系统] 暂无消息历史。\n")
        return

    print("\n" + "-" * 50)
    print("  🧾 消息历史")
    print("-" * 50)
    for msg in messages:
        role_label = "👤 输入" if msg["role"] == "user" else "🤖 模型"
        time_str = msg["timestamp"][:19].replace("T", " ")
        print(f"  [{time_str}] {role_label}:")
        print(f"    {summarize_text(msg['content'], preview_limit=160)}")
        print()
    print("-" * 50 + "\n")


def print_schedule_records(db: ChatDatabase):
    """打印最近的行程处理记录。"""
    records = db.get_recent_schedule_records(limit=10)
    if not records:
        print("[系统] 暂无行程处理记录。\n")
        return

    print("\n" + "-" * 60)
    print("  📌 最近处理记录")
    print("-" * 60)
    for record in records:
        time_str = record["timestamp"][:19].replace("T", " ")
        source_label = record["source_name"] or "直接输入"
        if SAFE_LOGGING and record["input_type"] == "file":
            source_label = "[文件名已隐藏]"
        print(f"  [{time_str}] 状态: {record['status']} | 来源: {record['input_type']} | 名称: {source_label}")
        print(f"    文本: {summarize_text(record['source_text'], preview_limit=120)}")
        if record["feishu_event_id"]:
            print(f"    event_id: {summarize_identifier(record['feishu_event_id'], label='event_id')}")
        if record["error_message"]:
            print(f"    错误: {record['error_message']}")
        print()
    print("-" * 60 + "\n")


def handle_command(command: str, db: ChatDatabase, llm: LLMClient, feishu: FeishuCalendarClient) -> bool:
    """
    处理用户命令
    :return: True 表示继续运行, False 表示退出
    """
    stripped = command.strip()
    cmd = stripped.lower()

    if cmd in {"/quit", "/exit"}:
        print("\n再见！👋")
        return False

    elif cmd == "/clear":
        db.clear_history()
        print("[系统] 消息历史与处理记录已清空。\n")

    elif cmd == "/history":
        print_message_history(db)

    elif cmd == "/records":
        print_schedule_records(db)

    elif cmd == "/count":
        message_count = db.get_message_count()
        schedule_count = db.get_schedule_count()
        print(f"[系统] 共有 {message_count} 条消息记录，{schedule_count} 条行程处理记录。\n")

    elif cmd == "/models":
        models = llm.get_available_models()
        if models:
            print("[系统] 可用模型:")
            for m in models:
                marker = " ← 当前" if MODEL_NAME in m else ""
                print(f"  - {m}{marker}")
            print()
        else:
            print("[系统] 未检测到模型。\n")

    elif cmd.startswith("/file"):
        file_path = stripped[5:].strip()
        try:
            source_name, file_content = read_text_file(file_path)
        except Exception as exc:
            if SAFE_LOGGING:
                print("[错误] 读取文件失败，请检查文件是否存在、可访问且编码正确。\n")
            else:
                print(f"[错误] 读取文件失败: {exc}\n")
        else:
            if SAFE_LOGGING:
                print("[系统] 已读取文件输入。\n")
            else:
                print(f"[系统] 已读取文件: {source_name}\n")
            process_schedule_text(
                source_text=file_content,
                input_type="file",
                source_name=source_name,
                db=db,
                llm=llm,
                feishu=feishu,
            )

    else:
        print(f"[系统] 未知命令: {command}\n")

    return True


def main():
    """主循环"""
    # 初始化组件
    db = ChatDatabase()
    llm = LLMClient()
    feishu = FeishuCalendarClient()

    # 打印横幅
    print_banner()

    # 启动检查
    if not startup_check(llm, feishu):
        print("\n[系统] 启动检查未通过，程序退出。")
        sys.exit(1)

    # 加载历史消息数量
    msg_count = db.get_message_count()
    schedule_count = db.get_schedule_count()
    if msg_count > 0 or schedule_count > 0:
        print(f"[系统] 已加载 {msg_count} 条消息、{schedule_count} 条处理记录。")
    print("\n请粘贴行程文字，或使用 /file 读取文件。\n")

    # 主循环
    while True:
        try:
            user_input = input("📝 输入: ").strip()

            if not user_input:
                continue

            if user_input.lower() in {"quit", "exit"}:
                print("\n再见！👋")
                break

            if user_input.startswith("/"):
                if not handle_command(user_input, db, llm, feishu):
                    break
                continue

            process_schedule_text(
                source_text=user_input,
                input_type="text",
                source_name="",
                db=db,
                llm=llm,
                feishu=feishu,
            )

        except KeyboardInterrupt:
            print("\n\n再见！👋")
            break
        except EOFError:
            print("\n\n再见！👋")
            break


if __name__ == "__main__":
    main()

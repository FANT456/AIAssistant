from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

from Feishu import *
from database import ChatDatabase
from LLMClient import LLMClient
from json_utils import ScheduleJsonError, normalize_schedule_json, to_pretty_json

def load_environment() -> Optional[Path]:
    discovered = find_dotenv(usecwd=True)
    if discovered:
        load_dotenv(discovered, override=False)
        return Path(discovered)

    fallback = ROOT_DIR / ".env"
    if fallback.exists():
        load_dotenv(fallback, override=False)
        return fallback

    return None


def env_str(name: str) -> str:
    return os.getenv(name, "").strip()


def print_banner():
    """打印启动横幅"""
    print("=" * 60)
    print("  📅 本地 AI 行程助手 (Phase 1)")
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


def process_schedule_text(
    source_text: str,
    input_type: str,
    source_name: str,
    db: ChatDatabase,
    llm: LLMClient,
    feishu: Feishu,
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


    print("[抽取结果] 标准 JSON 如下:")
    print(to_pretty_json(schedule_payload))
    print()

    print("[处理中] 正在写入飞书日历...")
    create_result,err = feishu.create_event(schedule_payload)

    if err is not None:
        print(f"[错误] 写入飞书日历失败: {err}\n")
        # db.save_schedule_record(
        #     input_type=input_type,
        #     source_name=source_name,
        #     source_text=clean_text,
        #     extracted_json=schedule_payload,
        #     status="feishu_failed",
        #     error_message=str(err),
        # )
        return
    if create_result.get("create_time") is not None:
        print("[成功] 已写入飞书日历 ✓")
        if create_result.get("event_id"):
            print(f"  event_id: {create_result.get('event_id')}")
        print()
        db.save_schedule_record(
            input_type=input_type,
            source_name=source_name,
            source_text=clean_text,
            extracted_json=schedule_payload,
            status="success",
            feishu_event_id=create_result.get("event_id"),
        )
    else:
        print(f"[错误] {create_result.error_message}\n")
        db.save_schedule_record(
            input_type=input_type,
            source_name=source_name,
            source_text=clean_text,
            extracted_json=schedule_payload,
            status="feishu_failed",
            error_message=create_result.get("msg"),
        )



def main() -> int:
    env_path = load_environment()
    if env_path:
        print(f"[环境] 已加载 .env: {env_path}")
    else:
        print("[环境] 未发现 .env，将仅使用当前环境变量")

    app_id = env_str("APP_ID")
    app_secret = env_str("APP_SECRET")
    calendar_id = env_str("CALENDAR_ID")
    summary = env_str("SUMMARY")
    start_time = env_str("START_TIME")
    end_time = env_str("END_TIME")
    db = ChatDatabase()
    llm = LLMClient()
    feishu = Feishu(
        app_id=app_id,
        app_secret=app_secret,
        calendar_id=calendar_id,
        summary=summary,
        start_time=start_time,
        end_time=end_time,
    )
    # return feishu.run()

    #   打印横幅
    print_banner()
    # 主循环
    while True:
        try:
            user_input = input("📝 输入: ").strip()

            if not user_input:
                continue

            if user_input.lower() in {"quit", "exit"}:
                print("\n再见！👋")
                break

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
    raise SystemExit(main())

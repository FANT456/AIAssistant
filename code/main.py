from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

from Feishu import *


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

    feishu = Feishu(
        app_id=app_id,
        app_secret=app_secret,
        calendar_id=calendar_id,
        summary=summary,
        start_time=start_time,
        end_time=end_time,
    )
    return feishu.run()


if __name__ == "__main__":
    raise SystemExit(main())

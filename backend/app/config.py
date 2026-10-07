"""应用配置：全部通过环境变量覆盖。"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "app.db"
FILES_DIR = DATA_DIR / "files"        # 原始 PDF
PUBLISHED_DIR = DATA_DIR / "published"  # 已发布的脱敏 PDF

ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "true").lower() in ("1", "true", "yes")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "72"))
SESSION_COOKIE = "redact_session"

# 前端静态文件目录（Docker 中由构建阶段产出）
STATIC_DIR = Path(os.environ.get("STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static"))).resolve()


def ensure_dirs() -> None:
    for d in (DATA_DIR, FILES_DIR, PUBLISHED_DIR):
        d.mkdir(parents=True, exist_ok=True)

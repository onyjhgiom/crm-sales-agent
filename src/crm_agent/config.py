"""配置与路径解析。

优先级：环境变量 > .env 文件 > 默认值。
不引入 python-dotenv，是为了让项目依赖保持最小——一个 20 行的解析函数就够了。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_dotenv(path: str | None = None) -> None:
    """极简 .env 解析：只支持 KEY=VALUE 与 # 注释，已存在的环境变量不覆盖。"""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    db_path: str
    knowledge_dir: str
    top_k: int = 4

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def mode(self) -> str:
        return "llm" if self.llm_enabled else "offline"


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1").strip(),
        model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
        db_path=os.getenv("CRM_DB_PATH", os.path.join(ROOT, "data", "crm.db")),
        knowledge_dir=os.getenv("KNOWLEDGE_DIR", os.path.join(ROOT, "data", "knowledge")),
        top_k=int(os.getenv("RAG_TOP_K", "4")),
    )

"""配置与路径解析。

优先级：环境变量 > .env 文件 > 默认值。
不引入 python-dotenv，是为了让项目依赖保持最小——一个 20 行的解析函数就够了。

生产相关参数集中在这里，并且全部做范围校验：填错类型或越界一律回落默认值，
不会因为一个坏配置让服务起不来（与 config/rules.json 的防呆思路一致）。
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


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    db_path: str
    knowledge_dir: str
    top_k: int = 4

    # ---- 运行模式与推理参数 ----
    # auto   : 有 Key 才在线（默认，保持"clone 即可跑"）
    # offline: 强制离线规则模式，不发起任何外部调用（演示 / 评估 / 无网环境）
    # llm    : 强制在线；缺 Key 时记错误并降级，不中断服务
    llm_mode: str = "auto"
    llm_timeout: float = 8.0          # 单次模型调用超时（秒）
    llm_max_retries: int = 1          # SDK 内部重试次数
    llm_max_tokens: int = 900
    llm_cb_threshold: int = 3         # 连续失败几次后熔断
    llm_cb_cooldown: float = 30.0     # 熔断冷却（秒）

    # ---- 服务稳定性参数 ----
    max_sessions: int = 256           # checkpointer 保留的最大会话数（FIFO 淘汰）
    # 默认保留 MemorySaver（多轮状态 + 作品集设计）；生产公网部署可设 false，
    # 编译无 checkpointer 的图，彻底消除会话状态写入与并发长尾。
    use_checkpointer: bool = True
    rate_limit_rpm: int = 60          # 单 IP 每分钟请求上限
    request_timeout: float = 15.0     # 单请求超时（秒）
    max_question_chars: int = 500

    # ---- 上下文记忆 ----
    memory_enabled: bool = True
    memory_max_turns: int = 12        # 单个会话保留的问答轮数
    memory_max_sessions: int = 128    # 进程的会话上界（LRU 淘汰）
    memory_answer_chars: int = 240    # 存进上下文的回答摘要长度
    memory_persist: bool = True       # 是否落盘，重启后仍记得
    memory_db_path: str = ""          # 由 load_settings 填默认值

    @property
    def llm_enabled(self) -> bool:
        if self.llm_mode == "offline":
            return False
        return bool(self.api_key)

    @property
    def mode(self) -> str:
        return "llm" if self.llm_enabled else "offline"


def load_settings() -> Settings:
    load_dotenv()
    mode = os.getenv("LLM_MODE", "auto").strip().lower()
    if mode not in {"auto", "offline", "llm"}:
        mode = "auto"
    return Settings(
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1").strip(),
        model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
        db_path=os.getenv("CRM_DB_PATH", os.path.join(ROOT, "data", "crm.db")),
        knowledge_dir=os.getenv("KNOWLEDGE_DIR", os.path.join(ROOT, "data", "knowledge")),
        top_k=_bounded_int("RAG_TOP_K", 4, 1, 20),
        llm_mode=mode,
        llm_timeout=_bounded_float("LLM_TIMEOUT", 8.0, 1.0, 60.0),
        llm_max_retries=_bounded_int("LLM_MAX_RETRIES", 1, 0, 3),
        llm_max_tokens=_bounded_int("LLM_MAX_TOKENS", 900, 100, 4000),
        llm_cb_threshold=_bounded_int("LLM_CB_THRESHOLD", 3, 1, 20),
        llm_cb_cooldown=_bounded_float("LLM_CB_COOLDOWN", 30.0, 1.0, 600.0),
        max_sessions=_bounded_int("MAX_SESSIONS", 256, 1, 10000),
        use_checkpointer=os.getenv("USE_CHECKPOINTER", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        rate_limit_rpm=_bounded_int("RATE_LIMIT_RPM", 60, 1, 10000),
        request_timeout=_bounded_float("REQUEST_TIMEOUT", 15.0, 1.0, 120.0),
        max_question_chars=_bounded_int("MAX_QUESTION_CHARS", 500, 20, 5000),
        memory_enabled=os.getenv("MEMORY_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        memory_max_turns=_bounded_int("MEMORY_MAX_TURNS", 12, 1, 100),
        memory_max_sessions=_bounded_int("MEMORY_MAX_SESSIONS", 128, 1, 10000),
        memory_answer_chars=_bounded_int("MEMORY_ANSWER_CHARS", 240, 40, 2000),
        memory_persist=os.getenv("MEMORY_PERSIST", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        memory_db_path=os.getenv("MEMORY_DB_PATH", os.path.join(ROOT, "data", "conversations.db")),
    )

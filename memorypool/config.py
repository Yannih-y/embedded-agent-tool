"""内存池默认配置。

混合方案：embedding 走本地（fastembed，ONNX，零服务），LLM 抽事实/固化走云 API（anthropic）。
向量库用 faiss（本地文件，零 docker）。长期记忆关系用 SQLite 三元组表（弃用 cognee 图谱）。
所有数据库文件由服务进程独占打开（决策10）。
"""

import os
from pathlib import Path

# 关掉 mem0 的 posthog 匿名遥测：它会往 us.i.posthog.com 发数据，
# 既拖慢（每次调用等超时）又往第三方发东西。必须在 import mem0 之前设。
os.environ.setdefault("MEM0_TELEMETRY", "false")
os.environ.setdefault("MEM0_TELEMETRY_ENABLED", "false")
os.environ.setdefault("POSTHOG_DISABLED", "true")

# 数据根目录：所有嵌入式库的文件都落在这里，服务进程独占
DATA_ROOT = Path(os.environ.get("MEMPOOL_DATA_ROOT", Path.home() / ".agent_memory_pool"))
DATA_ROOT.mkdir(parents=True, exist_ok=True)


# SQLite 库文件：版本/元数据权威源 + 长期记忆关系三元组表（服务进程独占）
DB_PATH = DATA_ROOT / "mempool.db"

# 本地 embedding 模型（fastembed 默认 gte-large，1024 维；可用 bge-small 更快）
EMBED_MODEL = os.environ.get("MEMPOOL_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIMS = int(os.environ.get("MEMPOOL_EMBED_DIMS", "384"))

# 云 LLM（抽事实/固化用，调用少、要质量）
LLM_PROVIDER = os.environ.get("MEMPOOL_LLM_PROVIDER", "anthropic")
LLM_MODEL = os.environ.get("MEMPOOL_LLM_MODEL", "claude-sonnet-4-5")


def _anthropic_api_key() -> str | None:
    """解析 anthropic key。标准 ANTHROPIC_API_KEY 优先；本机走中转配置时
    退回读 ANTHROPIC_AUTH_TOKEN（base_url 由 mem0 自己从 ANTHROPIC_BASE_URL 读）。
    """
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")


# 聚合网关：一个 key 通吃 claude/gpt/deepseek/glm...（已实测）。
# 网关按模型分两套协议：claude 系走 anthropic 端点 /v1/messages；
# 非 claude 走 OpenAI 端点 /v1/chat/completions（需 base_url 带 /v1）。
GATEWAY_BASE = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")


def gateway_key() -> str | None:
    """网关统一 key（多厂家共用同一个）。"""
    return _anthropic_api_key()


def anthropic_gateway_base() -> str:
    """claude 系走 anthropic 端点，用原始网关地址（不带 /v1）。"""
    return GATEWAY_BASE


def openai_gateway_base() -> str:
    """非 claude 走 OpenAI 端点，base_url 要带 /v1（SDK 会拼 /chat/completions）。"""
    return f"{GATEWAY_BASE}/v1" if GATEWAY_BASE else "https://api.openai.com/v1"


def list_gateway_models(timeout: float = 15.0) -> list[str]:
    """运行时从网关 /v1/models 拿真实可用模型清单。

    血泪教训：模型名不能写死。网关后台会随时上下架/改名——写死的
    gpt-4o/deepseek-3.2/claude-sonnet-4.5 某天就全 400「模型不支持」。
    可用清单只能运行时问网关，不能拍脑袋。
    """
    import httpx

    base, key = GATEWAY_BASE, gateway_key()
    if not base or not key:
        return []
    try:
        r = httpx.get(
            f"{base}/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def build_mem0_config() -> dict:
    """构造 mem0 Memory.from_config 用的配置字典。

    向量库 faiss + 本地 fastembed embedder + 云 anthropic LLM。
    """
    return {
        "vector_store": {
            "provider": "faiss",
            "config": {
                "collection_name": "mempool",
                "path": str(DATA_ROOT / "faiss"),
                "embedding_model_dims": EMBED_DIMS,
            },
        },
        "embedder": {
            "provider": "fastembed",
            "config": {
                "model": EMBED_MODEL,
                "embedding_dims": EMBED_DIMS,
            },
        },
        "llm": {
            "provider": LLM_PROVIDER,
            "config": {
                "model": LLM_MODEL,
                "api_key": _anthropic_api_key(),
            },
        },
    }

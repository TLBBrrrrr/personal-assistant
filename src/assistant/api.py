"""HTTP API：把对话能力暴露成 REST 服务，供网页/App 多终端访问。

设计要点：
- 每个 session_id 对应一个独立 Conversation，多终端互不串会话。
- X-API-Key 请求头鉴权；/health 免鉴权，便于探活与负载均衡。
- 复用 cli.py 的同一套初始化：Provider + Agent + 6 个工具 + post-turn hooks。
- 同步的 run_turn 放在线程池执行（FastAPI 的 def 路由天然如此），不阻塞事件循环。
- 每个会话一把锁，避免同一 session 并发请求导致消息历史竞争。

启动：
    PYTHONPATH=src uvicorn assistant.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from assistant.config import Settings
from assistant.core import Conversation, build_agent, log_turn_hook, make_record_hook
from assistant.providers import ModelProvider, build_provider
from assistant.recall import ModelEventExtractor, SQLiteEventStore
from assistant.recall.tools import register_recall_tools
from assistant.tools import ToolRegistry, build_default_registry


# ---------- 请求/响应模型 ----------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户输入")
    session_id: str = Field(default="default", description="会话标识，多终端用不同值隔离")
    reset: bool = Field(default=False, description="为 true 时清空该会话的历史")


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    turn_count: int
    hook_notes: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    provider: str
    sessions: int


# ---------- 会话管理 ----------
@dataclass
class _Session:
    conversation: Conversation
    lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class SessionStore:
    """session_id → Conversation 的内存会话表，带 TTL 惰性清理。"""

    def __init__(
        self,
        settings: Settings,
        provider: ModelProvider,
        registry: ToolRegistry,
        store: SQLiteEventStore,
        *,
        ttl_seconds: int = 86400,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._registry = registry
        self._store = store
        self._ttl = ttl_seconds
        self._sessions: dict[str, _Session] = {}
        self._guard = threading.Lock()

    def _new_conversation(self) -> Conversation:
        agent = build_agent(self._settings, self._registry, self._provider)
        conversation = Conversation(
            agent=agent, history_limit=self._settings.history_limit
        )
        if self._settings.hook_enabled:
            conversation.add_hook(log_turn_hook)
        if self._settings.auto_record:
            extractor = ModelEventExtractor(self._provider.get_model(), self._store)
            conversation.add_hook(make_record_hook(extractor))
        return conversation

    def get(self, session_id: str, *, reset: bool = False) -> _Session:
        with self._guard:
            self._evict_expired()
            if reset or session_id not in self._sessions:
                self._sessions[session_id] = _Session(
                    conversation=self._new_conversation()
                )
            session = self._sessions[session_id]
            session.last_used = time.time()
            return session

    def count(self) -> int:
        with self._guard:
            self._evict_expired()
            return len(self._sessions)

    def _evict_expired(self) -> None:
        cutoff = time.time() - self._ttl
        expired = [k for k, s in self._sessions.items() if s.last_used < cutoff]
        for k in expired:
            del self._sessions[k]


# ---------- 应用工厂 ----------
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    provider = build_provider(settings)
    registry = build_default_registry()
    store = SQLiteEventStore(settings.data_dir / settings.events_db)
    register_recall_tools(registry, store)
    sessions = SessionStore(settings, provider, registry, store)

    api_key = os.getenv("ASSISTANT_API_KEY_TOKEN", "").strip()
    cors_origins = [
        o.strip()
        for o in os.getenv("ASSISTANT_CORS_ORIGINS", "*").split(",")
        if o.strip()
    ]

    app = FastAPI(
        title="Personal Assistant API",
        version="0.1.0",
        description="个人助理对话接口：多终端会话隔离 + 工具调用 + 事件自动记录",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["POST", "GET"],
        allow_headers=["Content-Type", "X-API-Key"],
    )
    # 挂到 app.state，便于测试与运维检查
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.provider = provider

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if not api_key:
            # 未配置 token 时拒绝一切 /chat，避免误部署成公网裸奔
            raise HTTPException(status_code=503, detail="服务端未配置 API Token")
        if not x_api_key or not secrets.compare_digest(x_api_key, api_key):
            raise HTTPException(status_code=401, detail="无效或缺失的 API Key")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            provider=provider.description,
            sessions=sessions.count(),
        )

    @app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_key)])
    def chat(req: ChatRequest) -> ChatResponse:
        session = sessions.get(req.session_id, reset=req.reset)
        with session.lock:
            try:
                result = session.conversation.run_turn(req.message)
            except Exception as exc:  # 模型错误翻译成简洁提示，不泄露堆栈
                name = type(exc).__name__
                text = str(exc).splitlines()[0][:200] if str(exc) else ""
                raise HTTPException(
                    status_code=502, detail=f"模型调用失败（{name}）：{text}"
                )
            session.last_used = time.time()
            return ChatResponse(
                reply=result.output,
                session_id=req.session_id,
                turn_count=session.conversation.turn_count,
                hook_notes=result.hook_notes,
            )

    return app


app = create_app()


def run() -> None:
    """控制台入口：uvicorn 启动 HTTP 服务。"""
    import uvicorn

    host = os.getenv("ASSISTANT_API_HOST", "0.0.0.0")
    port = int(os.getenv("ASSISTANT_API_PORT", "8000"))
    uvicorn.run("assistant.api:app", host=host, port=port, log_level="info")

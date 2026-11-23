"""Garmin Connect 客户端：认证与连接的薄封装。

设计要点：
- 凭据来自 Settings，不硬编码；token 缓存到 token_dir，命中则免密、自动刷新。
- MFA 通过交互回调读取一次性验证码；非交互环境可用 return_on_mfa 由上层处理。
- 中国区账号用 is_cn 开关。
- 把底层异常翻译成简洁的中文提示，避免 CLI 满屏 traceback。
- 延迟 import garminconnect：没装依赖时不影响其他模块导入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from assistant.config import Settings


class GarminAuthError(RuntimeError):
    """Garmin 认证失败（缺凭据、MFA、风控、token 失效等）。"""


def _prompt_mfa() -> str:
    try:
        return input("Garmin 验证码（MFA）> ").strip()
    except (EOFError, KeyboardInterrupt):
        raise GarminAuthError("需要 MFA 验证码，但当前环境无法交互输入。")


class GarminClient:
    """持有已登录的 Garmin 会话，提供对底层 client 的访问。"""

    def __init__(self, client: Any, token_dir: Path) -> None:
        self._client = client
        self.token_dir = token_dir

    @property
    def raw(self) -> Any:
        """底层 garminconnect.Garmin 实例，供同步器调用各数据接口。"""
        return self._client

    def close(self) -> None:
        """仅清理本地 token 缓存，不撤销 Garmin 端 token。"""
        try:
            self._client.logout(str(self.token_dir))
        except Exception:  # noqa: BLE001 - 登出失败无需影响主流程
            pass

    def __enter__(self) -> "GarminClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def connect(
    settings: Settings,
    *,
    prompt_mfa: Callable[[], str] | None = None,
    interactive: bool = True,
) -> GarminClient:
    """登录 Garmin Connect，返回客户端。

    - 优先用 token 缓存（token_dir），命中且有效则免账号密码。
    - 缓存缺失/失效时用 GARMIN_EMAIL / GARMIN_PASSWORD 登录。
    - MFA：interactive=True 时用回调读取验证码。
    """
    try:
        from garminconnect import Garmin
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise GarminAuthError(
            "缺少 garminconnect 依赖，请先安装："
            ".venv/bin/pip install garminconnect curl_cffi"
        ) from exc

    if not settings.garmin_email or not settings.garmin_password:
        raise GarminAuthError(
            "未配置 Garmin 凭据。请在 .env 设置 GARMIN_EMAIL 和 GARMIN_PASSWORD。"
        )

    token_dir = Path(settings.garmin_token_dir)
    token_dir.mkdir(parents=True, exist_ok=True)

    client = Garmin(
        email=settings.garmin_email,
        password=settings.garmin_password,
        is_cn=settings.garmin_is_cn,
        prompt_mfa=(prompt_mfa or _prompt_mfa) if interactive else None,
        return_on_mfa=not interactive,
    )

    try:
        status, _ = client.login(str(token_dir))
    except GarminAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一翻译底层认证异常
        raise _translate_auth_error(exc) from exc

    if status == "needs_mfa":
        raise GarminAuthError(
            "该账号需要 MFA，但当前为非交互模式；请用交互模式登录一次以缓存 token。"
        )

    return GarminClient(client, token_dir)


def _translate_auth_error(exc: Exception) -> GarminAuthError:
    text = str(exc)
    name = type(exc).__name__
    if "429" in text or "TooManyRequests" in name:
        return GarminAuthError(
            "Garmin 返回 429（请求过于频繁/风控拦截），请稍后再试，避免短时间反复登录。"
        )
    if "401" in text or "403" in text or "Authentication" in name:
        return GarminAuthError(
            "Garmin 鉴权失败：账号密码错误、触发风控校验，或 token 已失效。"
            "可删除 token 缓存目录后重试："
            + "rm -rf data/garmin_tokens/*"
        )
    if any(k in name for k in ("Connection", "Timeout", "Network")):
        return GarminAuthError(
            "连接 Garmin 失败：请检查网络；国内网络可能需要代理，"
            "或确认 is_cn 是否与账号区域一致。"
        )
    return GarminAuthError(f"Garmin 登录失败（{name}）：{text.splitlines()[0][:160]}")

"""Garmin Connect Personal API 客户端（官方，中国区）。

两步认证：
- 用 Personal API Key（.env_garmin 里的裸 key）POST 换 JWT；
- 数据端点用 Bearer JWT 调用。

设计要点：
- key 只从文件读入内存，绝不打印、绝不进日志；JWT 在实例内缓存。
- base URL 固定中国区 personal.connectapi.garmin.cn（该 key 为中国区签发）。
- 只依赖标准库 urllib，不引入第三方 HTTP 依赖。
- 底层异常翻译成简洁中文提示；响应体只取 message 字段，避免泄露凭据。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 中国区 Personal API 入口（key 为中国区签发；国际区 .com 会返回 key 不存在）
DEFAULT_BASE_URL = "https://personal.connectapi.garmin.cn"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"


class GarminAPIError(RuntimeError):
    """Garmin Personal API 调用失败（凭据/认证/限流/网络）。"""


def load_api_key(path: str | Path) -> str:
    """从文件读取裸 key（一行）。"""
    p = Path(path)
    if not p.exists():
        raise GarminAPIError(
            f"未找到 Garmin Personal API Key 文件：{p}。"
            "请在 Garmin Connect 网页创建 key，并把裸 key 单独一行存入该文件。"
        )
    key = p.read_text(encoding="utf-8").strip()
    if not key:
        raise GarminAPIError(f"Garmin API Key 文件为空：{p}")
    return key


class PersonalAPIClient:
    """持有 JWT 的轻量 API 客户端。用 with 语句或手动 close()。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token: str | None = None

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
    ) -> "PersonalAPIClient":
        return cls(load_api_key(path), base_url=base_url, timeout=timeout)

    # ---------- 认证 ----------
    def _fetch_token(self) -> str:
        """用 API Key 换取 JWT；响应体是裸字符串，不是 JSON。"""
        url = self.base_url + TOKEN_PATH
        req = urllib.request.Request(
            url, method="POST", headers={"Authorization": f"ApiKey {self._api_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                token = resp.read().decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as exc:
            raise self._translate(exc) from exc
        except urllib.error.URLError as exc:
            raise GarminAPIError(f"连接 Garmin 失败：{exc.reason}。请检查网络。") from exc
        if not token:
            raise GarminAPIError("换取 access token 成功但返回为空。")
        # 记录 token 前缀长度用于调试，但不打印内容本身
        if not token.count(".") >= 2:
            raise GarminAPIError(
                f"换取 access token 返回格式异常（非 JWT，长度 {len(token)}）。"
            )
        return token

    def token(self) -> str:
        """返回缓存的 JWT，没有则换取。"""
        if self._token is None:
            self._token = self._fetch_token()
        return self._token

    # ---------- 数据调用 ----------
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET 一个 JSON 端点；path 已含前导斜杠，params 自动编码。"""
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # token 可能过期，失效一次后重试一次
                self._token = None
                req = urllib.request.Request(
                    url,
                    method="GET",
                    headers={
                        "Authorization": f"Bearer {self.token()}",
                        "Accept": "application/json",
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        raw = resp.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as exc2:
                    raise self._translate(exc2) from exc2
                except urllib.error.URLError as exc2:
                    raise GarminAPIError(f"连接 Garmin 失败：{exc2.reason}。") from exc2
            else:
                raise self._translate(exc) from exc
        except urllib.error.URLError as exc:
            raise GarminAPIError(f"连接 Garmin 失败：{exc.reason}。请检查网络。") from exc

        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GarminAPIError(f"端点 {path} 返回的不是合法 JSON。") from exc

    # ---------- 异常翻译 ----------
    @staticmethod
    def _translate(exc: urllib.error.HTTPError) -> GarminAPIError:
        code = exc.code
        message = ""
        try:
            body = exc.read().decode("utf-8", "replace")
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("error") or "")[:120]
        except Exception:  # noqa: BLE001 - 解析错误体失败不影响主提示
            pass
        hint = f"（{message}）" if message else ""
        if code == 404:
            return GarminAPIError(
                f"Garmin 返回 404{hint}。请确认 key 有效且为中国区；"
                "国际区 key 在 .cn 端点会报 key 不存在。"
            )
        if code in (401, 403):
            return GarminAPIError(
                f"Garmin 鉴权失败{hint}。key 可能已过期或无该数据权限，"
                "请到 Garmin Connect 网页重新生成 key。"
            )
        if code == 429:
            return GarminAPIError(
                f"Garmin 返回 429（请求过于频繁）{hint}。请稍后重试，或增大请求间隔。"
            )
        return GarminAPIError(f"Garmin 返回 HTTP {code}{hint}。")

    def close(self) -> None:
        self._token = None

    def __enter__(self) -> "PersonalAPIClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

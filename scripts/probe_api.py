"""连通性自检：读取本地 .env，探测候选端点，列模型，跑一次真实链路。

用法：
    .venv/bin/python scripts/probe_api.py

密钥只从 .env / 环境变量读取，绝不出现在命令行或日志里。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 让脚本能 import 项目内的 config
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assistant.config import Settings  # noqa: E402

# 腾讯云候选 OpenAI 兼容端点（探测顺序）
CANDIDATE_BASES = [
    "https://tokenhub.tencentmaas.com/v1",
    "https://api.hunyuan.cloud.tencent.com/v1",
    "https://tokenhub.tencentcloudmaas.com/v1",
    "https://tokenhub-intl.tencentcloudmaas.com/v1",
    "https://tokenhub-us.tencentcloudmaas.tech/v1",
    "https://hunyuan.cloud.tencent.com/openai/v1",
    "https://yuanbao.tencentcloudapi.com/v1",
]

# 这些是功能型模型，不是对话模型，从列表里过滤掉
NOISE = ("embedding", "image", "audio", "vl", "asr", "tts", "ocr", "video", "rerank")

# 判定鉴权用的探测模型（即使模型名不对，也能靠错误类型区分 401 与 404）
PROBE_MODEL = "hunyuan-turbo"


def _http_json(url: str, api_key: str, payload: dict | None = None, timeout: int = 30):
    """发一个 Bearer 鉴权的 JSON 请求，返回 (status, data_or_text)。"""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST" if body else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw[:500]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:500]
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def mask(key: str) -> str:
    return key[:10] + "…" if key else "(空)"


def is_chat_ok(data) -> bool:
    """真正的聊天响应必须有 choices；腾讯云网关错误会返回 200 + Response.Error。"""
    return isinstance(data, dict) and bool(data.get("choices"))


def main() -> int:
    cfg = Settings.from_env(ROOT / ".env")

    print("=== 生效配置 ===")
    print(f"配置文件   : {ROOT / '.env'}  (存在: {(ROOT / '.env').exists()})")
    print(f"Provider   : {cfg.provider}")
    print(f"API Base   : {cfg.api_base or '(未配置)'}")
    print(f"模型       : {cfg.model}")
    print(f"API Key    : {mask(cfg.api_key or '')}  已配置: {bool(cfg.api_key)}")
    print()

    if not cfg.api_key:
        print("[未配置密钥] 跳过联网探测（应用会降级到 test provider，不报错）")
        return 0

    # 把配置的 base 放到候选列表最前，去重
    bases = list(dict.fromkeys(
        ([cfg.api_base] if cfg.api_base else []) + CANDIDATE_BASES
    ))

    # 1) 探测哪个端点能列出模型
    working_base: str | None = None
    chat_models: list[str] = []
    print("=== 探测端点 /models ===")
    for base in bases:
        url = base.rstrip("/") + "/models"
        status, data = _http_json(url, cfg.api_key)
        if status == 200 and isinstance(data, dict) and "data" in data:
            ids = [m.get("id", "") for m in data["data"] if isinstance(m, dict)]
            chat_models = [i for i in ids if i and not any(n in i.lower() for n in NOISE)]
            working_base = base
            print(f"  ✓ {base}  -> {len(ids)} 个模型（对话类 {len(chat_models)}）")
            for m in chat_models[:12]:
                print(f"       - {m}")
            break
        else:
            brief = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:160]
            print(f"  ✗ {base}  -> HTTP {status}: {brief}")

    # 1b) 若 /models 全失败，直接打 chat/completions 做判定性诊断
    #     有些兼容端点不支持 /models，但 chat 能通；靠错误类型区分「鉴权失败」与「模型名不对」
    if not working_base:
        print("\n=== /models 未通过，改用 chat/completions 做判定诊断 ===")
        for base in bases:
            url = base.rstrip("/") + "/chat/completions"
            status, data = _http_json(
                url,
                cfg.api_key,
                payload={
                    "model": PROBE_MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 8,
                },
                timeout=40,
            )
            brief = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:200]
            if status == 200 and is_chat_ok(data):
                print(f"  ✓ {base}  -> 200 鉴权通过且模型可用！")
                working_base = base
                chat_models = [PROBE_MODEL]
                break
            elif status == 200:
                print(f"  ✗ {base}  -> HTTP 200 但非聊天响应（疑似网关错误）: {brief}")
            elif "model" in brief.lower() and ("not" in brief.lower() or "exist" in brief.lower()):
                print(f"  ~ {base}  -> HTTP {status} 鉴权通过，但模型名不对: {brief}")
                working_base = base
                chat_models = [PROBE_MODEL]
                break
            else:
                print(f"  ✗ {base}  -> HTTP {status}: {brief}")

    if not working_base:
        print("\n[失败] 所有候选端点鉴权均未通过（全部 401）。")
        print("结论：这个密钥不属于上述任何一个腾讯端点，或密钥已失效/未开通模型权限。")
        print("请确认：")
        print("  1. 密钥从哪个控制台创建（混元 / TokenHub / 元宝）——不同站点密钥不通用")
        print("  2. 密钥是否处于「启用」状态、是否已开通对应模型调用权限")
        return 1

    # 2) 选一个真实模型名（去掉 LiteLLM 的 openai/ 前缀）
    configured_model = cfg.model.split("/", 1)[-1] if "/" in cfg.model else cfg.model
    if chat_models and configured_model not in chat_models:
        model = chat_models[0]
        print(f"\n提示：配置的 {configured_model!r} 不在可用列表，改用 {model!r}")
    else:
        model = configured_model

    # 3) 真实 payload 跑一次完整链路
    print(f"\n=== 真实链路测试 ===")
    print(f"端点: {working_base.rstrip('/')}/chat/completions   模型: {model}")
    status, data = _http_json(
        working_base.rstrip("/") + "/chat/completions",
        cfg.api_key,
        payload={
            "model": model,
            "messages": [{"role": "user", "content": "只回复两个字：收到"}],
            "max_tokens": 32,
        },
        timeout=60,
    )
    if status == 200 and is_chat_ok(data):
        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            answer = json.dumps(data, ensure_ascii=False)[:300]
        print(f"  ✓ HTTP 200  回复: {answer!r}")
        usage = data.get("usage")
        if usage:
            print(f"  usage: {json.dumps(usage, ensure_ascii=False)}")
    else:
        brief = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:400]
        print(f"  ✗ HTTP {status}: {brief}")
        return 1

    print("\n=== 自检通过 ===")
    print(f"建议把 .env 更新为：")
    print(f"  ASSISTANT_API_BASE={working_base}")
    print(f"  ASSISTANT_MODEL=openai/{model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

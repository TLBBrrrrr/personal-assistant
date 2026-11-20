"""事件抽取器（Phase 1B）：从一轮对话中抽取值得记录的生活事件。

设计要点（来自 02 文档 3.1 节）：
- 保守判定：寒暄、纯问答、知识查询不记；只记经历/感受/决定/计划/带时空人物的事实。
- 结构化输出：让模型按 EventDraft 输出，Pydantic 校验，避免自由文本解析。
- 时间解析：把"昨天/上周"等相对描述解析为 occurred_at（以当前时间为参考）。
- 去重：与近期同标题事件比对，重复则跳过。
- 失败不打断：任何异常都被吞掉并返回空列表，主对话不受影响。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from assistant.models import Event
from assistant.recall.sqlite_store import SQLiteEventStore


class EventDraft(BaseModel):
    """模型输出的单条事件草稿（尚未落库）。"""

    title: str
    content: str = ""
    type: str = "experience"
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    mood: str | None = None
    importance: float = 0.5
    when_hint: str = ""


class ExtractionResult(BaseModel):
    worth_recording: bool = False
    reason: str = ""
    events: list[EventDraft] = Field(default_factory=list)


EXTRACT_INSTRUCTIONS = """你是一个生活事件记录助手。判断用户这一轮的话里有没有值得长期记住的事情。

值得记（worth_recording=true）：
- 经历、感受、想法、决定、计划（如"昨天去跑步很爽""我打算下个月辞职"）
- 带具体时间/地点/人物的事实（如"周六和张三吃了饭"）
- 里程碑事件、情绪状态

不值得记（worth_recording=false）：
- 寒暄、打招呼、闲聊（如"你好""在吗""谢谢"）
- 纯知识问答、让助理做事的指令（如"现在几点""帮我查天气""退出"）
- 助理自己说的话、高度重复的内容

判定时只依据"用户说的话"。值得记时，抽取为 events 列表，每条给出：
- title：一句话概括（20 字内）
- content：原始经历/感受的完整描述
- type：experience/feeling/note/decision/milestone 之一
- tags、entities：主题标签与相关人/事
- mood：情绪词或 null
- importance：0-1 的重要度
- when_hint：用户提到的时间，如"昨天""上周六"；没提就留空

宁可漏记，不要误记。寒暄一律 worth_recording=false。"""


class EventExtractor(Protocol):
    def extract(self, user_input: str, assistant_output: str = "") -> list[Event]: ...


def resolve_when(hint: str, now: datetime | None = None) -> datetime:
    """把中文相对时间描述解析为绝对时间；无法识别则返回 now。

    支持：今天/现在/刚刚/昨天/前天/大前天/上周/上周X/周X/N天前/N周前/X月X日。
    """
    base = now or datetime.now().astimezone()
    hint = (hint or "").strip()
    if not hint:
        return base

    if any(k in hint for k in ("现在", "刚刚", "今天", "今日")):
        return base
    if "大前天" in hint:
        return base - timedelta(days=3)
    if "前天" in hint:
        return base - timedelta(days=2)
    if "昨天" in hint or "昨日" in hint:
        return base - timedelta(days=1)

    m = re.search(r"(\d+|[一二三四五六七八九十两])天前", hint)
    if m:
        n = _cn_num(m.group(1))
        if n:
            return base - timedelta(days=n)

    m = re.search(r"(\d+|[一二三四五六七八九十两])周前", hint)
    if m:
        n = _cn_num(m.group(1))
        if n:
            return base - timedelta(weeks=n)

    if "上周" in hint or "上星期" in hint:
        weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        wd = next((v for k, v in weekdays.items() if k in hint), None)
        if wd is not None:
            # 本周一 = base - base.weekday()，再往前一周，加目标星期
            this_monday = base - timedelta(days=base.weekday())
            return (this_monday - timedelta(weeks=1) + timedelta(days=wd)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
        return base - timedelta(weeks=1)

    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", hint)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            return base.replace(month=month, day=day, hour=9, minute=0, second=0, microsecond=0)
        except ValueError:
            return base

    return base


def _cn_num(text: str) -> int | None:
    mapping = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    if text.isdigit():
        return int(text)
    return mapping.get(text)


class ModelEventExtractor:
    """用模型结构化抽取事件，并落库到 EventStore。"""

    def __init__(
        self,
        model: Model,
        store: SQLiteEventStore,
        dedup_hours: int = 24,
    ) -> None:
        self.store = store
        self.dedup_hours = dedup_hours
        self._agent = Agent(model, output_type=ExtractionResult, instructions=EXTRACT_INSTRUCTIONS)

    def extract(self, user_input: str, assistant_output: str = "") -> list[Event]:
        """抽取并落库；任何失败都返回空列表，绝不抛出异常打断对话。"""
        try:
            result = self._agent.run_sync(
                f"用户说：{user_input}\n\n助理回复：{assistant_output}"
            )
            data: ExtractionResult = result.output
            if not data.worth_recording:
                return []

            now = datetime.now().astimezone()
            saved: list[Event] = []
            for draft in data.events:
                if not draft.title.strip():
                    continue
                if self.store.similar_exists(draft.title, within_hours=self.dedup_hours):
                    continue
                event = Event(
                    id="",
                    occurred_at=resolve_when(draft.when_hint, now),
                    recorded_at=now,
                    source="dialogue",
                    type=draft.type or "experience",
                    title=draft.title.strip(),
                    content=draft.content or draft.title,
                    tags=draft.tags[:8],
                    entities=draft.entities[:8],
                    mood=draft.mood,
                    importance=max(0.0, min(1.0, draft.importance)),
                )
                self.store.add(event)
                saved.append(event)
            return saved
        except Exception:
            # 抽取是后处理，失败绝不能影响主对话
            return []


class StaticEventExtractor:
    """测试用的确定性抽取器：把用户输入直接作为一条事件。"""

    def __init__(self, store: SQLiteEventStore) -> None:
        self.store = store

    def extract(self, user_input: str, assistant_output: str = "") -> list[Event]:
        if not user_input.strip():
            return []
        now = datetime.now().astimezone()
        event = Event(
            id="",
            occurred_at=now,
            recorded_at=now,
            source="dialogue",
            type="experience",
            title=user_input[:40],
            content=user_input,
        )
        self.store.add(event)
        return [event]

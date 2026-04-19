"""
Decision Log Service - NT531 Nhóm 17

API:
  POST /decisions       -- Agent (Part 4) ghi quyết định mới
  GET  /decisions       -- list (filter by status, limit, offset)
  GET  /decisions/{id}  -- chi tiết
  PATCH /decisions/{id} -- review label / human override
  GET  /metrics         -- Prometheus metrics

Schema:
  id (UUID)
  ts (timestamp)
  alert_name (str)
  decision (str: rollback | scale | restart | suggest | no_action)
  confidence (float 0..1)
  reasoning (text)
  action_taken (bool)
  human_override (bool)
  review_label (str: correct | incorrect | partially_correct | unreviewed)
  llm_model (str)
  llm_latency_ms (int)
  context_metrics (json text)

Storage: SQLite (file mount PVC) – đổi sang Postgres trong production.
"""

import json
import os
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session

DB_URL = os.getenv("DB_URL", "sqlite:////data/decisions.db")
engine = create_engine(DB_URL, echo=False, future=True)


class Base(DeclarativeBase):
    pass


class Decision(Base):
    __tablename__ = "decisions"
    id = Column(String, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    alert_name = Column(String, index=True)
    decision = Column(String, index=True)
    confidence = Column(Float)
    reasoning = Column(Text)
    action_taken = Column(Boolean, default=False)
    human_override = Column(Boolean, default=False)
    review_label = Column(String, default="unreviewed", index=True)
    llm_model = Column(String, default="")
    llm_latency_ms = Column(Integer, default=0)
    context_metrics = Column(Text, default="{}")  # JSON string


Base.metadata.create_all(engine)


# ---- Prometheus metrics ----
registry = CollectorRegistry()
decisions_total = Counter(
    "ai_agent_decision_total",
    "Tổng số decision đã ghi log",
    ["decision", "review_label"],
    registry=registry,
)
override_total = Counter(
    "ai_agent_override_total",
    "Tổng số quyết định bị kỹ sư override",
    registry=registry,
)
false_positive_total = Counter(
    "ai_agent_false_positive_total",
    "Tổng số quyết định bị review là false positive (incorrect)",
    registry=registry,
)
confidence_hist = Histogram(
    "ai_agent_confidence_score",
    "Phân phối confidence score của decisions",
    buckets=(0.1, 0.25, 0.5, 0.7, 0.85, 0.9, 0.95, 0.99, 1.0),
    registry=registry,
)
llm_latency_hist = Histogram(
    "ai_agent_llm_request_duration_seconds",
    "Thời gian gọi LLM",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60),
    registry=registry,
)
decisions_in_db = Gauge(
    "ai_agent_decisions_total_in_db",
    "Tổng số decision trong database",
    registry=registry,
)


# ---- Pydantic schemas ----
class DecisionCreate(BaseModel):
    alert_name: str
    decision: str = Field(..., pattern=r"^(rollback|scale|restart|suggest|no_action)$")
    confidence: float = Field(..., ge=0, le=1)
    reasoning: str = ""
    action_taken: bool = False
    llm_model: str = ""
    llm_latency_ms: int = 0
    context_metrics: dict = Field(default_factory=dict)


class DecisionUpdate(BaseModel):
    review_label: Optional[str] = Field(
        None, pattern=r"^(correct|incorrect|partially_correct|unreviewed)$"
    )
    human_override: Optional[bool] = None
    action_taken: Optional[bool] = None


class DecisionOut(BaseModel):
    id: str
    ts: datetime
    alert_name: str
    decision: str
    confidence: float
    reasoning: str
    action_taken: bool
    human_override: bool
    review_label: str
    llm_model: str
    llm_latency_ms: int
    context_metrics: dict


# ---- FastAPI app ----
app = FastAPI(title="Decision Log - NT531 Nhóm 17")


def to_out(d: Decision) -> DecisionOut:
    try:
        ctx = json.loads(d.context_metrics or "{}")
    except json.JSONDecodeError:
        ctx = {}
    return DecisionOut(
        id=d.id,
        ts=d.ts,
        alert_name=d.alert_name,
        decision=d.decision,
        confidence=d.confidence,
        reasoning=d.reasoning,
        action_taken=d.action_taken,
        human_override=d.human_override,
        review_label=d.review_label,
        llm_model=d.llm_model,
        llm_latency_ms=d.llm_latency_ms,
        context_metrics=ctx,
    )


@app.post("/decisions", response_model=DecisionOut, status_code=201)
def create_decision(payload: DecisionCreate):
    d_id = str(uuid.uuid4())
    with Session(engine) as s:
        d = Decision(
            id=d_id,
            ts=datetime.utcnow(),
            alert_name=payload.alert_name,
            decision=payload.decision,
            confidence=payload.confidence,
            reasoning=payload.reasoning,
            action_taken=payload.action_taken,
            human_override=False,
            review_label="unreviewed",
            llm_model=payload.llm_model,
            llm_latency_ms=payload.llm_latency_ms,
            context_metrics=json.dumps(payload.context_metrics),
        )
        s.add(d)
        s.commit()
        decisions_total.labels(decision=payload.decision, review_label="unreviewed").inc()
        confidence_hist.observe(payload.confidence)
        if payload.llm_latency_ms > 0:
            llm_latency_hist.observe(payload.llm_latency_ms / 1000)
        s.refresh(d)
        return to_out(d)


@app.get("/decisions", response_model=list[DecisionOut])
def list_decisions(
    review_label: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    with Session(engine) as s:
        stmt = select(Decision).order_by(Decision.ts.desc())
        if review_label:
            stmt = stmt.where(Decision.review_label == review_label)
        if decision:
            stmt = stmt.where(Decision.decision == decision)
        stmt = stmt.limit(limit).offset(offset)
        rows = s.execute(stmt).scalars().all()
        return [to_out(d) for d in rows]


@app.get("/decisions/{decision_id}", response_model=DecisionOut)
def get_decision(decision_id: str):
    with Session(engine) as s:
        d = s.get(Decision, decision_id)
        if not d:
            raise HTTPException(404, "decision not found")
        return to_out(d)


@app.patch("/decisions/{decision_id}", response_model=DecisionOut)
def update_decision(decision_id: str, payload: DecisionUpdate):
    with Session(engine) as s:
        d = s.get(Decision, decision_id)
        if not d:
            raise HTTPException(404, "decision not found")
        if payload.review_label is not None:
            old = d.review_label
            d.review_label = payload.review_label
            decisions_total.labels(decision=d.decision, review_label=payload.review_label).inc()
            if payload.review_label == "incorrect" and old != "incorrect":
                false_positive_total.inc()
        if payload.human_override is not None:
            if payload.human_override and not d.human_override:
                override_total.inc()
            d.human_override = payload.human_override
        if payload.action_taken is not None:
            d.action_taken = payload.action_taken
        s.commit()
        s.refresh(d)
        return to_out(d)


@app.get("/metrics")
def metrics():
    # Refresh gauge từ DB count
    with Session(engine) as s:
        cnt = s.execute(select(func.count(Decision.id))).scalar() or 0
        decisions_in_db.set(cnt)
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    with Session(engine) as s:
        cnt = s.execute(select(func.count(Decision.id))).scalar() or 0
    return {"status": "ok", "decisions_count": cnt}


@app.get("/")
def root():
    return {
        "service": "decision-log",
        "project": "NT531 Nhóm 17 - skeleton ready for Part 4 Agent",
        "endpoints": ["/decisions", "/metrics", "/healthz"],
    }

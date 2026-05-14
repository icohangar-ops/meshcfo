# Multi-Agent CFO OS — CockroachDB Persistence Layer
"""
SQLAlchemy ORM for distributed storage of CFO OS decision cases, forecasts,
investment memos, board outputs, and audit trails. Reuses CHP models.
"""
from __future__ import annotations
import os, logging
from sqlalchemy import create_engine, Column, String, Integer, Numeric, DateTime, Text, Boolean, Index, JSON, func, select, desc
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB

logger = logging.getLogger("multi_agent_cfo_os.db")

COCKROACH_URL = "cockroachdb+psycopg2://cubiczan:oY-hPkgXtZjc6kGqY67Gyg@vortex-giraffe-15678.jxf.gcp-us-east1.cockroachlabs.cloud:26257/multi_agent_cfo_os?sslmode=require"
DATABASE_URL = os.getenv("MACFO_DATABASE_URL", COCKROACH_URL)
engine = create_engine(DATABASE_URL, pool_size=8, max_overflow=4, pool_timeout=30, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)

def get_session() -> Session: return SessionLocal()

Base = declarative_base()

class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CFOBriefModel(TimestampMixin, Base):
    """Input briefs for CFO OS sessions."""
    __tablename__ = "cfo_briefs"
    brief_id = Column(String, primary_key=True, server_default=func.gen_random_uuid())
    title = Column(String, nullable=False)
    company = Column(String, default="")
    problem = Column(Text, default="")
    horizon = Column(String, default="FY")
    owner = Column(String, default="cfo")
    task_type = Column(String, default="forecast")  # forecast, investment_case, board_output
    high_stakes = Column(Boolean, default=True)
    origin_system = Column(String, default="Claude")
    origin_model = Column(String, default="GPT-5.4")
    partner_system = Column(String, default="Partner")
    decision_id = Column(String, default="")
    brief_data = Column(JSONB, default={})  # full brief as JSON

    artifacts = relationship("CFOArtifactModel", back_populates="brief_rel", cascade="all, delete-orphan")
    audit_trails = relationship("CFOAuditModel", back_populates="brief_rel", cascade="all, delete-orphan")


class CFOArtifactModel(TimestampMixin, Base):
    """CFO-grade output artifacts."""
    __tablename__ = "cfo_artifacts"
    artifact_id = Column(String, primary_key=True, server_default=func.gen_random_uuid())
    brief_id = Column(String, nullable=False, index=True)
    artifact_type = Column(String, nullable=False)  # forecast_pack, investment_case_memo, board_output
    title = Column(String, nullable=False)
    lock_state = Column(String, default="")
    sections = Column(JSONB, default=[])
    rendered_markdown = Column(Text, default="")
    brief_rel = relationship("CFOBriefModel", back_populates="artifacts")


class CFOAuditModel(TimestampMixin, Base):
    """Per-claim audit trail entries."""
    __tablename__ = "cfo_audit"
    audit_id = Column(String, primary_key=True, server_default=func.gen_random_uuid())
    brief_id = Column(String, nullable=False, index=True)
    agent = Column(String, default="")
    claim = Column(Text, default="")
    expansion_label = Column(String, default="")
    expansion_excerpt = Column(Text, default="")
    grounding_source = Column(String, default="")
    grounding_confidence = Column(String, default="")
    risk_flag = Column(String, default="")
    foundation_findings = Column(JSONB, default=[])
    structural_vulnerabilities = Column(JSONB, default=[])
    failure_modes = Column(JSONB, default=[])
    brief_rel = relationship("CFOBriefModel", back_populates="audit_trails")

    __table_args__ = (Index("ix_cfo_audit_agent", "agent"),)


class DecisionCaseModel(TimestampMixin, Base):
    """CHP-hardened decision cases (mirrors sec-earnings-workbench CHP models)."""
    __tablename__ = "decision_cases"
    decision_id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    domain = Column(String, default="")
    owner = Column(String, default="")
    status = Column(String, default="EXPLORING", index=True)
    high_stakes = Column(Boolean, default=False)
    current_phase = Column(String, default="FOUNDATION")
    current_round = Column(Integer, default=0)
    origin_system = Column(String, default="Claude")
    partner_system = Column(String, default="Partner")
    foundation_score = Column(Integer, nullable=True)
    locked_decisions = Column(JSONB, default=[])
    structural_vulnerabilities = Column(JSONB, default=[])
    dossier = Column(JSONB, default={})
    rounds = relationship("RoundRecordModel", back_populates="case_rel", cascade="all, delete-orphan")


class RoundRecordModel(TimestampMixin, Base):
    __tablename__ = "round_records"
    round_id = Column(String, primary_key=True, server_default=func.gen_random_uuid())
    decision_id = Column(String, nullable=False, index=True)
    phase = Column(String, default="FOUNDATION")
    round_number = Column(Integer, default=0)
    payload_id = Column(String, default="")
    origin_packet = Column(Text, default="")
    partner_packet = Column(Text, default="")
    verdict = Column(String, default="")
    state_snapshot = Column(JSONB, default={})
    case_rel = relationship("DecisionCaseModel", back_populates="rounds")


class ForecastModel(TimestampMixin, Base):
    """Forecast outputs with driver-level detail."""
    __tablename__ = "forecasts"
    forecast_id = Column(String, primary_key=True, server_default=func.gen_random_uuid())
    brief_id = Column(String, nullable=False, index=True)
    company = Column(String, default="")
    horizon = Column(String, default="FY")
    base_revenue_usd = Column(Numeric(18, 2), default=0)
    base_opex_usd = Column(Numeric(18, 2), default=0)
    growth_assumption_pct = Column(Numeric(6, 4), default=0)
    churn_assumption_pct = Column(Numeric(6, 4), default=0)
    projected_revenue_usd = Column(Numeric(18, 2), default=0)
    projected_opex_usd = Column(Numeric(18, 2), default=0)
    projected_runway_months = Column(Integer, default=0)
    stress_downside = Column(JSONB, default={})
    stress_upside = Column(JSONB, default={})
    lock_state = Column(String, default="")


# Repositories
class CFOBriefRepository:
    @staticmethod
    def get_all(session: Session) -> list[dict]:
        rows = session.execute(select(CFOBriefModel).order_by(desc(CFOBriefModel.created_at))).scalars().all()
        return [{"id": r.brief_id, "title": r.title, "type": r.task_type, "company": r.company, "high_stakes": r.high_stakes, "status": r.lock_state or "draft", "created": str(r.created_at)} for r in rows]

    @staticmethod
    def get_by_type(session: Session, task_type: str) -> list[dict]:
        rows = session.execute(select(CFOBriefModel).where(CFOBriefModel.task_type == task_type).order_by(desc(CFOBriefModel.created_at))).scalars().all()
        return [{"id": r.brief_id, "title": r.title, "company": r.company} for r in rows]


def health_check() -> dict:
    session = get_session()
    try:
        row = session.execute(func.current_timestamp()).scalar()
        briefs = session.execute(select(func.count()).select_from(CFOBriefModel)).scalar()
        artifacts = session.execute(select(func.count()).select_from(CFOArtifactModel)).scalar()
        decisions = session.execute(select(func.count()).select_from(DecisionCaseModel)).scalar()
        return {"status": "ok", "connected": True, "server_time": str(row), "cfo_briefs": briefs, "cfo_artifacts": artifacts, "decision_cases": decisions, "backend": "CockroachDB"}
    except Exception as e:
        return {"status": "error", "connected": False, "error": str(e)}
    finally:
        session.close()

def create_tables():
    Base.metadata.create_all(bind=engine)
    logger.info("All tables created successfully")

if __name__ == "__main__":
    create_tables()
    print(health_check())

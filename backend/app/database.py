"""Conexão com o banco de dados.

Usamos SQLite no protótipo (decisão da Sprint 01: "SQLite para protótipo local").
Como todo acesso passa pelo SQLAlchemy, trocar para PostgreSQL é só mudar a
variável de ambiente EVCO_DB_URL, por exemplo:
    postgresql+psycopg://usuario:senha@localhost:5432/evchargeops
"""
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# pasta onde ficam o banco SQLite e os modelos de IA treinados (.joblib)
PASTA_DADOS = Path(os.getenv("EVCO_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
PASTA_DADOS.mkdir(parents=True, exist_ok=True)

DB_URL = os.getenv("EVCO_DB_URL", f"sqlite:///{PASTA_DADOS / 'evchargeops.db'}")

# check_same_thread=False é necessário no SQLite porque o FastAPI atende
# requisições em threads diferentes.
_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def criar_tabelas() -> None:
    # import local para registrar os modelos antes do create_all
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)


def get_db():
    """Dependência do FastAPI: abre uma sessão por requisição e fecha no final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

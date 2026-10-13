import os
import tempfile

# Os testes usam uma pasta temporária: não mexem no banco nem nos modelos de demonstração.
_PASTA = tempfile.mkdtemp(prefix="evco_testes_")
os.environ["EVCO_DATA_DIR"] = _PASTA
os.environ["EVCO_DB_URL"] = f"sqlite:///{_PASTA}/teste.db"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.ia import servico as ia  # noqa: E402
from app.seed import popular_cadastro  # noqa: E402


@pytest.fixture
def db():
    """Banco em memória novo para cada teste, já com o cadastro base."""
    from app import models  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessao = sessionmaker(bind=engine, expire_on_commit=False)()
    popular_cadastro(sessao)
    ia.resetar_modelos()
    yield sessao
    sessao.close()

"""API do EV ChargeOps (FastAPI).

Rodar (dentro da pasta backend):
    uvicorn app.main:app --reload
Documentação interativa: http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import SessionLocal, criar_tabelas
from app.ia.servico import treinar_se_necessario
from app.routers import faturas, ia, integracoes, notificacoes, ocpp, painel, sessoes
from app.seed import popular_cadastro

# mostra no console da API os avisos do EnviadorConsole (app/notificacoes.py)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    # garante tabelas e cadastro base mesmo se o simulador ainda não rodou
    criar_tabelas()
    with SessionLocal() as db:
        popular_cadastro(db)
        # banco com histórico mas sem modelo salvo: treina agora em vez de esperar a próxima fatura
        try:
            treinar_se_necessario(db)
        except Exception:  # a API sobe mesmo assim, só com as regras
            logging.getLogger("evchargeops.ia").exception("treino inicial da IA falhou")
    yield


app = FastAPI(
    title="EV ChargeOps API",
    description="Sessões de recarga, rateio transparente e IA operacional — Enterprise Challenge 2026 (GoodWe/FIAP)",
    version="0.2.0",
    lifespan=ciclo_de_vida,
)

# o front-end (Vite) roda em outra porta durante o desenvolvimento
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessoes.router)
app.include_router(faturas.router)
app.include_router(painel.router)
app.include_router(ia.router)
app.include_router(notificacoes.router)
app.include_router(integracoes.router)
app.include_router(ocpp.router)


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok"}

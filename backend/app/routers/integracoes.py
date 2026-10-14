"""Rotas das integrações com a nuvem do fabricante (SEMS)."""
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.integracoes import sems
from app.seed import CARREGADOR_ID
from app.sessoes import ErroSessao

router = APIRouter(prefix="/integracoes", tags=["Integrações"])


@router.post("/sems/sincronizar")
def sincronizar_sems(carregador_id: str = CARREGADOR_ID, db: Session = Depends(get_db)):
    """Lê o carregador no SEMS e grava a leitura na sessão aberta, se houver."""
    cliente = sems.ClienteSEMS.do_ambiente()
    planta_id = os.getenv("EVCO_SEMS_PLANTA_ID")
    if cliente is None or not planta_id:
        raise HTTPException(
            503, "SEMS não configurado: defina EVCO_SEMS_USUARIO, EVCO_SEMS_SENHA e EVCO_SEMS_PLANTA_ID"
        )
    try:
        return sems.sincronizar_sems(db, cliente, planta_id, carregador_id)
    except (httpx.HTTPError, RuntimeError, ValueError) as erro:
        raise HTTPException(502, f"Falha ao ler o SEMS: {erro}") from erro
    except ErroSessao as erro:
        raise HTTPException(erro.status_http, str(erro)) from erro

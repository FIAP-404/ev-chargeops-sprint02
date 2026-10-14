"""Rotas do ciclo de vida da sessão (usadas pelo carregador/gateway e pelo app)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app import schemas, sessoes
from app.database import get_db
from app.faturamento import limites_do_mes
from app.serializacao import sessao_dict

router = APIRouter(prefix="/sessoes", tags=["Sessões"])


def _executar(funcao, *args, **kwargs):
    """Traduz erros de regra de negócio em respostas HTTP."""
    try:
        return funcao(*args, **kwargs)
    except sessoes.ErroSessao as erro:
        raise HTTPException(status_code=erro.status_http, detail=str(erro)) from erro


@router.post("/iniciar", status_code=201)
def iniciar(body: schemas.IniciarSessao, db: Session = Depends(get_db)):
    s = _executar(
        sessoes.iniciar_sessao, db, body.carregador_id, body.codigo_rfid, body.inicio_em, body.leitura_inicial_kwh
    )
    return sessao_dict(s)


@router.post("/{sessao_id}/telemetria")
def telemetria(sessao_id: int, body: schemas.Telemetria, db: Session = Depends(get_db)):
    s = _executar(
        sessoes.registrar_telemetria,
        db, sessao_id, body.timestamp, body.potencia_kw, body.energia_acumulada_kwh, body.status_carregador,
    )
    return {"sessao_id": s.id, "leitura_atual_kwh": s.leitura_final_kwh, "potencia_max_kw": s.potencia_max_kw}


@router.post("/{sessao_id}/encerrar")
def encerrar(sessao_id: int, body: schemas.EncerrarSessao, db: Session = Depends(get_db)):
    s = _executar(sessoes.encerrar_sessao, db, sessao_id, body.motivo, body.fim_em, body.leitura_final_kwh)
    return sessao_dict(s)


@router.post("/{sessao_id}/revisao")
def revisar(sessao_id: int, body: schemas.RevisaoSessao, db: Session = Depends(get_db)):
    s = _executar(
        sessoes.revisar_sessao, db, sessao_id, body.decisao, body.usuario_id, body.ajuste_valor, body.observacao
    )
    return sessao_dict(s)


@router.get("")
def listar(
    mes: str | None = None,
    unidade_id: int | None = None,
    usuario_id: int | None = None,
    status_cobranca: str | None = None,
    limite: int = 200,
    db: Session = Depends(get_db),
):
    q = select(m.SessaoRecarga).order_by(m.SessaoRecarga.inicio_em.desc()).limit(limite)
    if mes:
        try:
            inicio, fim = limites_do_mes(mes)
        except ValueError as erro:
            raise HTTPException(400, str(erro)) from erro
        q = q.where(m.SessaoRecarga.inicio_em >= inicio, m.SessaoRecarga.inicio_em < fim)
    if unidade_id:
        q = q.where(m.SessaoRecarga.unidade_id == unidade_id)
    if usuario_id:
        q = q.where(m.SessaoRecarga.usuario_id == usuario_id)
    if status_cobranca:
        q = q.where(m.SessaoRecarga.status_cobranca.in_(status_cobranca.split(",")))
    return [sessao_dict(s) for s in db.scalars(q)]


@router.get("/{sessao_id}")
def detalhe(sessao_id: int, db: Session = Depends(get_db)):
    s = db.get(m.SessaoRecarga, sessao_id)
    if s is None:
        raise HTTPException(404, "Sessão não encontrada")
    return sessao_dict(s, com_eventos=True)

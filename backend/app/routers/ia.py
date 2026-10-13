"""Rotas do módulo de IA."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app import schemas
from app.database import get_db
from app.ia import servico as ia
from app.serializacao import alerta_dict

router = APIRouter(prefix="/ia", tags=["IA"])


@router.get("/status")
def status():
    det, prev = ia.detector(), ia.previsor()
    return {
        "anomalias": {
            "treinado": det.treinado,
            "sessoes_treino": det.n_treino,
            "treinado_em": det.treinado_em.isoformat(timespec="minutes") if det.treinado_em else None,
            "tecnica": "Regras de negócio + Isolation Forest (scikit-learn)",
        },
        "demanda": {
            "treinado": prev.treinado,
            "tecnica": "Regressão de Poisson (dia × faixa, hora, tendência semanal)",
            "metricas": prev.metricas,
        },
    }


@router.post("/treinar")
def treinar(db: Session = Depends(get_db)):
    resultado = ia.treinar_modelos(db)
    ia.atualizar_alertas_capacidade(db)
    return resultado


@router.get("/previsao-capacidade")
def previsao_capacidade(horizonte_semanas: int = 12, db: Session = Depends(get_db)):
    return ia.previsao_capacidade(db, datetime.now(), horizonte_semanas)


@router.get("/alertas")
def alertas(status: str | None = "aberto", limite: int = 100, db: Session = Depends(get_db)):
    q = select(m.AlertaIA).order_by(m.AlertaIA.criado_em.desc()).limit(limite)
    if status:
        q = q.where(m.AlertaIA.status == status)
    return [alerta_dict(a) for a in db.scalars(q)]


@router.post("/alertas/{alerta_id}/resolver")
def resolver(alerta_id: int, body: schemas.ResolverAlerta, db: Session = Depends(get_db)):
    alerta = db.get(m.AlertaIA, alerta_id)
    if alerta is None:
        raise HTTPException(404, "Alerta não encontrado")
    sessao = db.get(m.SessaoRecarga, alerta.sessao_id) if alerta.sessao_id else None
    if sessao is not None and sessao.status_cobranca in (m.COBRANCA_REVISAO_IA, m.COBRANCA_QUARENTENA):
        raise HTTPException(409, "Este alerta retém uma sessão: use /sessoes/{id}/revisao para decidir")
    alerta.status, alerta.resolucao = "resolvido", body.resolucao
    db.commit()
    return alerta_dict(alerta)

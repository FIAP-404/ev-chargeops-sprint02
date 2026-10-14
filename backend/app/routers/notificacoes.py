"""Caixa de avisos do morador (resumo das recargas, ociosidade e decisões do gestor)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.database import get_db
from app.serializacao import notificacao_dict

router = APIRouter(tags=["Avisos ao morador"])


@router.get("/usuarios/{usuario_id}/notificacoes")
def listar(usuario_id: int, nao_lidas: bool = False, limite: int = 20, db: Session = Depends(get_db)):
    if db.get(m.Usuario, usuario_id) is None:
        raise HTTPException(404, "Usuário não encontrado")
    # não lidos primeiro, depois do mais recente para o mais antigo
    q = (
        select(m.Notificacao)
        .where(m.Notificacao.usuario_id == usuario_id)
        .order_by(m.Notificacao.lida, m.Notificacao.criado_em.desc(), m.Notificacao.id.desc())
        .limit(limite)
    )
    if nao_lidas:
        q = q.where(m.Notificacao.lida.is_(False))
    return [notificacao_dict(n) for n in db.scalars(q)]


@router.post("/notificacoes/{notificacao_id}/lida")
def marcar_lida(notificacao_id: int, db: Session = Depends(get_db)):
    n = db.get(m.Notificacao, notificacao_id)
    if n is None:
        raise HTTPException(404, "Aviso não encontrado")
    # marcar de novo não muda o horário da primeira leitura
    if not n.lida:
        n.lida, n.lida_em = True, datetime.now()
        db.commit()
    return notificacao_dict(n)

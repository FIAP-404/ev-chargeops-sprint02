"""Rotas de faturamento, cadastro e parâmetros de rateio."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app import schemas
from app.database import get_db
from app.faturamento import gerar_faturas, obter_parametros
from app.serializacao import fatura_dict, parametros_dict

router = APIRouter(tags=["Faturas e cadastro"])


@router.post("/faturas/gerar")
def gerar(mes: str, db: Session = Depends(get_db)):
    try:
        faturas = gerar_faturas(db, mes)
    except ValueError as erro:
        raise HTTPException(400, str(erro)) from erro
    return [fatura_dict(f) for f in faturas]


@router.get("/faturas")
def listar(mes: str | None = None, unidade_id: int | None = None, db: Session = Depends(get_db)):
    q = select(m.Fatura).order_by(m.Fatura.mes_referencia.desc(), m.Fatura.valor_total.desc())
    if mes:
        q = q.where(m.Fatura.mes_referencia == mes)
    if unidade_id:
        q = q.where(m.Fatura.unidade_id == unidade_id)
    return [fatura_dict(f) for f in db.scalars(q)]


@router.get("/faturas/{fatura_id}")
def detalhe(fatura_id: int, db: Session = Depends(get_db)):
    f = db.get(m.Fatura, fatura_id)
    if f is None:
        raise HTTPException(404, "Fatura não encontrada")
    return fatura_dict(f)


@router.get("/parametros")
def ler_parametros(db: Session = Depends(get_db)):
    return parametros_dict(obter_parametros(db))


@router.put("/parametros")
def atualizar_parametros(body: schemas.Parametros, db: Session = Depends(get_db)):
    p = obter_parametros(db)
    for campo, valor in body.model_dump().items():
        setattr(p, campo, valor)
    db.commit()
    return parametros_dict(p)


@router.get("/usuarios")
def usuarios(db: Session = Depends(get_db)):
    lista = db.scalars(select(m.Usuario).order_by(m.Usuario.nome))
    return [
        {
            "id": u.id,
            "nome": u.nome,
            "email": u.email,
            "perfil": u.perfil,
            "unidade_id": u.unidade_id,
            "unidade": u.unidade.rotulo if u.unidade else None,
            "veiculos": [f"{v.modelo} ({v.placa})" for v in u.veiculos],
        }
        for u in lista
    ]


@router.get("/unidades")
def unidades(db: Session = Depends(get_db)):
    return [
        {"id": u.id, "rotulo": u.rotulo, "bloco": u.bloco, "numero": u.numero}
        for u in db.scalars(select(m.Unidade).order_by(m.Unidade.bloco, m.Unidade.numero))
    ]


@router.get("/carregadores")
def carregadores(db: Session = Depends(get_db)):
    return [
        {
            "id": c.id,
            "fabricante": c.fabricante,
            "modelo": c.modelo,
            "localizacao": c.localizacao,
            "potencia_max_kw": c.potencia_max_kw,
            "status": c.status,
        }
        for c in db.scalars(select(m.Carregador))
    ]

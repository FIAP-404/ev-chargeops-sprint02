"""Rotas de faturamento, cadastro e parâmetros de rateio."""
import csv
import io
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import Integer, cast, select
from sqlalchemy.orm import Session

from app import models as m
from app import schemas
from app.database import get_db
from app.faturamento import gerar_faturas, obter_parametros
from app.ia import servico as ia
from app.serializacao import fatura_dict, parametros_dict

router = APIRouter(tags=["Faturas e cadastro"])
log = logging.getLogger("evchargeops.ia")


@router.post("/faturas/gerar")
def gerar(mes: str, db: Session = Depends(get_db)):
    try:
        faturas = gerar_faturas(db, mes)
    except ValueError as erro:
        raise HTTPException(400, str(erro)) from erro
    # fechou o mês: os modelos aprendem com as sessões que acabaram de ser liberadas/faturadas.
    # Fica aqui e não em gerar_faturas() porque o simulador retreina com o relógio simulado.
    try:
        info = ia.treinar_modelos(db)
        if info["demanda"]["treinado"]:
            ia.atualizar_alertas_capacidade(db)
    except Exception:  # a fatura já foi gerada; o retreino tenta de novo no próximo mês
        db.rollback()
        log.exception("retreino da IA após gerar as faturas falhou")
    return [fatura_dict(f) for f in faturas]


def _validar_mes(mes: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
        raise HTTPException(400, "mes deve estar no formato AAAA-MM")


@router.get("/faturas")
def listar(mes: str | None = None, unidade_id: int | None = None, db: Session = Depends(get_db)):
    q = select(m.Fatura).order_by(m.Fatura.mes_referencia.desc(), m.Fatura.valor_total.desc())
    if mes:
        _validar_mes(mes)
        q = q.where(m.Fatura.mes_referencia == mes)
    if unidade_id:
        q = q.where(m.Fatura.unidade_id == unidade_id)
    return [fatura_dict(f) for f in db.scalars(q)]


def _num(valor: float, casas: int = 2) -> str:
    """Número no padrão brasileiro (vírgula decimal), para o Excel abrir certo."""
    return f"{valor:.{casas}f}".replace(".", ",")


# antes de /faturas/{fatura_id}: senão o Starlette casa {fatura_id} e devolve 422
@router.get("/faturas/exportar.csv")
def exportar_csv(mes: str, db: Session = Depends(get_db)):
    """Faturas do mês em CSV, para a administradora lançar no boleto do condomínio."""
    _validar_mes(mes)
    # numero é texto: ordena como número para A-302 vir antes de A-1204
    faturas = db.scalars(
        select(m.Fatura).join(m.Unidade).where(m.Fatura.mes_referencia == mes)
        .order_by(m.Unidade.bloco, cast(m.Unidade.numero, Integer), m.Unidade.numero)
    ).all()
    if not faturas:
        raise HTTPException(404, "Nenhuma fatura gerada para este mês")

    buf = io.StringIO()
    escritor = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    escritor.writerow([
        "unidade", "mes", "qtd_sessoes", "kwh_total", "energia", "perdas", "infra",
        "ociosidade", "ajustes", "total", "status", "sessoes_pendentes",
    ])
    for f in faturas:
        # ajustes vai positivo: é o crédito já abatido do total, como na tela
        escritor.writerow([
            f.unidade.rotulo, f.mes_referencia, f.qtd_sessoes, _num(f.kwh_total, 3),
            _num(f.valor_energia), _num(f.valor_perdas), _num(f.valor_infra), _num(f.valor_ociosidade),
            _num(f.ajustes), _num(f.valor_total), f.status, f.sessoes_pendentes,
        ])
    # utf-8-sig: com o BOM o Excel reconhece os acentos
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="faturas-{mes}.csv"'},
    )


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
            # usado pelo app para escolher o veículo ao ler o QR Code
            "veiculos_detalhe": [
                {"id": v.id, "modelo": v.modelo, "placa": v.placa, "potencia_ac_max_kw": v.potencia_ac_max_kw}
                for v in u.veiculos
            ],
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

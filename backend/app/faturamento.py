"""Geração de faturas por unidade, usando o motor de rateio.

Liga o banco (sessões liberadas no mês) ao cálculo puro de app/rateio.py.
"""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.rateio import ParametrosCalculo, SessaoRateio, calcular_rateio


def limites_do_mes(mes: str) -> tuple[datetime, datetime]:
    """'2026-08' -> (2026-08-01 00:00, 2026-09-01 00:00)."""
    try:
        ano, mm = (int(x) for x in mes.split("-"))
        inicio = datetime(ano, mm, 1)
    except ValueError as exc:
        raise ValueError("mes deve estar no formato AAAA-MM") from exc
    fim = datetime(ano + (mm == 12), 1 if mm == 12 else mm + 1, 1)
    return inicio, fim


def obter_parametros(db: Session) -> m.ParametrosRateio:
    params = db.get(m.ParametrosRateio, 1)
    if params is None:
        params = m.ParametrosRateio(id=1)
        db.add(params)
        db.commit()
    return params


def parametros_calculo(p: m.ParametrosRateio) -> ParametrosCalculo:
    return ParametrosCalculo(
        tarifa_kwh=p.tarifa_kwh,
        perdas_percentual=p.perdas_percentual,
        taxa_infra=p.taxa_infra,
        cobrar_infra_sem_uso=p.cobrar_infra_sem_uso,
        ociosidade_ativa=p.ociosidade_ativa,
        tolerancia_ociosidade_min=p.tolerancia_ociosidade_min,
        penalidade_ociosidade_hora=p.penalidade_ociosidade_hora,
    )


def valor_da_sessao(db: Session, sessao: m.SessaoRecarga) -> float:
    """Quanto a sessão soma à fatura antes de créditos: energia + perdas + ociosidade.

    Usa o mesmo motor de rateio da fatura. A taxa de infraestrutura fica de fora
    porque é mensal e por unidade, não da sessão. É o teto do crédito do gestor.
    """
    base = SessaoRateio(sessao_id=sessao.id, kwh=sessao.kwh_entregue or 0.0, minutos_ociosos=sessao.minutos_ociosos or 0.0)
    r = calcular_rateio([base], parametros_calculo(obter_parametros(db)))
    return float(r.valor_total - r.valor_infra)


def _sessoes_da_unidade(db: Session, unidade_id: int, mes: str, status: tuple[str, ...]) -> list[m.SessaoRecarga]:
    inicio, fim = limites_do_mes(mes)
    return list(
        db.scalars(
            select(m.SessaoRecarga)
            .where(
                m.SessaoRecarga.unidade_id == unidade_id,
                m.SessaoRecarga.inicio_em >= inicio,
                m.SessaoRecarga.inicio_em < fim,
                m.SessaoRecarga.status_cobranca.in_(status),
            )
            .order_by(m.SessaoRecarga.inicio_em)
        )
    )


def _para_rateio(sessoes: list[m.SessaoRecarga]) -> list[SessaoRateio]:
    return [
        SessaoRateio(
            sessao_id=s.id,
            kwh=s.kwh_entregue,
            minutos_ociosos=s.minutos_ociosos,
            ajuste_valor=s.ajuste_valor,
            veiculo=s.veiculo.placa if s.veiculo else "",
        )
        for s in sessoes
    ]


def calcular_unidade(db: Session, unidade_id: int, mes: str) -> dict:
    """Prévia da fatura (não grava nada). Usada na área do usuário durante o mês."""
    sessoes = _sessoes_da_unidade(db, unidade_id, mes, (m.COBRANCA_LIBERADA, m.COBRANCA_FATURADA))
    pendentes = _sessoes_da_unidade(db, unidade_id, mes, (m.COBRANCA_REVISAO_IA,))
    resultado = calcular_rateio(_para_rateio(sessoes), parametros_calculo(obter_parametros(db)))
    dados = resultado.como_dict()
    dados["sessoes_ids"] = [s.id for s in sessoes]
    dados["sessoes_pendentes"] = [s.id for s in pendentes]
    return dados


def gerar_faturas(db: Session, mes: str) -> list[m.Fatura]:
    """Fecha o mês: gera (ou recalcula) a fatura de todas as unidades.

    Sessões em quarentena/revisão não entram; ficam contadas em
    sessoes_pendentes e, se o gestor liberar depois, basta gerar de novo.
    """
    params = parametros_calculo(obter_parametros(db))
    faturas = []
    agora = datetime.now()
    for unidade in db.scalars(select(m.Unidade).order_by(m.Unidade.bloco, m.Unidade.numero)):
        sessoes = _sessoes_da_unidade(db, unidade.id, mes, (m.COBRANCA_LIBERADA, m.COBRANCA_FATURADA))
        pendentes = _sessoes_da_unidade(db, unidade.id, mes, (m.COBRANCA_REVISAO_IA,))
        r = calcular_rateio(_para_rateio(sessoes), params)

        fatura = db.scalar(
            select(m.Fatura).where(m.Fatura.unidade_id == unidade.id, m.Fatura.mes_referencia == mes)
        ) or m.Fatura(unidade_id=unidade.id, mes_referencia=mes)
        fatura.qtd_sessoes = r.qtd_sessoes
        fatura.kwh_total = float(r.kwh_total)
        fatura.valor_energia = float(r.valor_energia)
        fatura.valor_perdas = float(r.valor_perdas)
        fatura.valor_infra = float(r.valor_infra)
        fatura.valor_ociosidade = float(r.valor_ociosidade)
        fatura.ajustes = float(r.ajustes)
        fatura.valor_total = float(r.valor_total)
        fatura.sessoes_pendentes = len(pendentes)
        fatura.status = "fechada" if not pendentes else "fechada_com_pendencias"
        fatura.gerada_em = agora
        fatura.memoria_calculo = json.dumps(
            {"linhas": r.memoria, "kwh_por_veiculo": r.por_veiculo, "sessoes": [s.id for s in sessoes]},
            ensure_ascii=False,
        )
        db.add(fatura)
        db.flush()
        for s in sessoes:
            s.status_cobranca = m.COBRANCA_FATURADA
            s.fatura_id = fatura.id
        faturas.append(fatura)
    db.commit()
    return faturas

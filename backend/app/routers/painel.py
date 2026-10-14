"""Dados agregados para o dashboard do gestor e para a área do usuário."""
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.database import get_db
from app.faturamento import calcular_unidade, limites_do_mes, obter_parametros, parametros_calculo, valor_da_sessao
from app.ia import servico as ia
from app.rateio import SessaoRateio, calcular_rateio
from app.serializacao import fatura_dict, sessao_dict

router = APIRouter(tags=["Painéis"])

NAO_COBRADAS = (m.COBRANCA_DESCARTADA,)


def mes_corrente() -> str:
    return datetime.now().strftime("%Y-%m")


def _consumo_por_carregador(db: Session, sessoes, validas, tentativas, horas_no_mes: float) -> list[dict]:
    """Mesmo recorte dos KPIs, separado por carregador. valor_sessoes = soma de
    valor_da_sessao (energia + perdas + ociosidade), sem a taxa de infraestrutura,
    que é mensal e por unidade."""
    itens = []
    for c in db.scalars(select(m.Carregador).order_by(m.Carregador.id)):
        todas = [s for s in sessoes if s.carregador_id == c.id]
        val = [s for s in validas if s.carregador_id == c.id]
        itens.append({
            "carregador_id": c.id,
            "fabricante": c.fabricante,
            "modelo": c.modelo,
            "localizacao": c.localizacao,
            "sessoes": len(val),
            "kwh": round(sum(s.kwh_entregue for s in val), 2),
            "minutos_ociosos": round(sum(s.minutos_ociosos for s in val)),
            "tentativas_ocupado": sum(1 for _, cid in tentativas if cid == c.id),
            # mesma base do KPI de ocupação: todas as sessões, inclusive as descartadas
            "ocupacao_pct": round(min(sum(s.duracao_min for s in todas) / 60 / horas_no_mes, 1) * 100, 1),
            "valor_sessoes": round(sum(valor_da_sessao(db, s) for s in val), 2),
        })
    return itens


@router.get("/dashboard/resumo")
def resumo(mes: str | None = None, db: Session = Depends(get_db)):
    mes = mes or mes_corrente()
    try:
        inicio, fim = limites_do_mes(mes)
    except ValueError as erro:
        raise HTTPException(400, str(erro)) from erro
    agora = datetime.now()
    sessoes = list(
        db.scalars(
            select(m.SessaoRecarga).where(m.SessaoRecarga.inicio_em >= inicio, m.SessaoRecarga.inicio_em < fim)
        )
    )
    validas = [s for s in sessoes if s.status_cobranca not in NAO_COBRADAS]
    tentativas = db.execute(
        select(m.TentativaOcupado.timestamp, m.TentativaOcupado.carregador_id).where(
            m.TentativaOcupado.timestamp >= inicio, m.TentativaOcupado.timestamp < fim
        )
    ).all()

    # rateio de cada unidade no mês (prévia ou fatura fechada, o cálculo é o mesmo)
    por_unidade = []
    for u in db.scalars(select(m.Unidade).order_by(m.Unidade.bloco, m.Unidade.numero)):
        calc = calcular_unidade(db, u.id, mes)
        por_unidade.append({
            "unidade_id": u.id,
            "unidade": u.rotulo,
            "sessoes": calc["qtd_sessoes"],
            "kwh": round(calc["kwh_total"], 2),
            "valor_total": calc["valor_total"],
            "valor_ociosidade": calc["valor_ociosidade"],
            "pendentes": len(calc["sessoes_pendentes"]),
        })
    por_unidade.sort(key=lambda x: -x["kwh"])

    diario: dict[str, dict] = defaultdict(lambda: {"kwh": 0.0, "sessoes": 0})
    chegadas_hora = [0] * 24
    for s in validas:
        d = diario[s.inicio_em.date().isoformat()]
        d["kwh"] += s.kwh_entregue
        d["sessoes"] += 1
    for s in sessoes:
        chegadas_hora[s.inicio_em.hour] += 1
    for ts, _ in tentativas:
        chegadas_hora[ts.hour] += 1

    horas_no_mes = max(((min(agora, fim) if inicio <= agora else fim) - inicio).total_seconds() / 3600, 1)
    horas_ocupado = sum(s.duracao_min for s in sessoes) / 60
    retidas = [s for s in sessoes if s.status_cobranca in (m.COBRANCA_QUARENTENA, m.COBRANCA_REVISAO_IA)]
    # relatório por carregador (Sprint 01, 5.2 passo 10)
    por_carregador = _consumo_por_carregador(db, sessoes, validas, tentativas, horas_no_mes)

    return {
        "mes": mes,
        "kpis": {
            "sessoes": len(validas),
            "kwh": round(sum(s.kwh_entregue for s in validas), 1),
            "valor_rateado": round(sum(u["valor_total"] for u in por_unidade), 2),
            "ocupacao_pct": round(min(horas_ocupado / horas_no_mes, 1) * 100, 1),
            "tentativas_ocupado": len(tentativas),
            "sessoes_retidas": len(retidas),
            "minutos_ociosos": round(sum(s.minutos_ociosos for s in validas)),
            "unidades_ativas": sum(1 for u in por_unidade if u["sessoes"] > 0),
        },
        "consumo_por_unidade": por_unidade,
        "consumo_por_carregador": por_carregador,
        "consumo_diario": [
            {"data": d, "kwh": round(v["kwh"], 2), "sessoes": v["sessoes"]} for d, v in sorted(diario.items())
        ],
        "chegadas_por_hora": [{"hora": h, "chegadas": c} for h, c in enumerate(chegadas_hora)],
        # valor_sessao: teto do crédito que o gestor pode conceder na decisão
        "sessoes_retidas": [{**sessao_dict(s), "valor_sessao": valor_da_sessao(db, s)} for s in retidas],
    }


@router.get("/dashboard/historico-mensal")
def historico_mensal(db: Session = Depends(get_db)):
    meses: dict[str, dict] = defaultdict(lambda: {"sessoes": 0, "kwh": 0.0, "tentativas_ocupado": 0, "valor_faturado": 0.0})
    for s in db.scalars(select(m.SessaoRecarga)):
        if s.status_cobranca in NAO_COBRADAS:
            continue
        d = meses[s.inicio_em.strftime("%Y-%m")]
        d["sessoes"] += 1
        d["kwh"] += s.kwh_entregue
    for ts in db.scalars(select(m.TentativaOcupado.timestamp)):
        meses[ts.strftime("%Y-%m")]["tentativas_ocupado"] += 1
    for f in db.scalars(select(m.Fatura)):
        meses[f.mes_referencia]["valor_faturado"] += f.valor_total
    return [
        {"mes": mes, **{k: round(v, 2) if isinstance(v, float) else v for k, v in dados.items()},
         "fechado": dados["valor_faturado"] > 0}
        for mes, dados in sorted(meses.items())
    ]


@router.get("/usuarios/{usuario_id}/painel")
def painel_usuario(usuario_id: int, mes: str | None = None, db: Session = Depends(get_db)):
    usuario = db.get(m.Usuario, usuario_id)
    if usuario is None or usuario.unidade_id is None:
        raise HTTPException(404, "Morador não encontrado")
    mes = mes or mes_corrente()
    try:
        inicio, fim = limites_do_mes(mes)
    except ValueError as erro:
        raise HTTPException(400, str(erro)) from erro
    unidade = usuario.unidade

    sessoes = list(
        db.scalars(
            select(m.SessaoRecarga)
            .where(
                m.SessaoRecarga.unidade_id == unidade.id,
                m.SessaoRecarga.inicio_em >= inicio,
                m.SessaoRecarga.inicio_em < fim,
            )
            .order_by(m.SessaoRecarga.inicio_em.desc())
        )
    )
    parcial = calcular_unidade(db, unidade.id, mes)

    # Projeção da IA: quanto a fatura deve fechar se o padrão de uso continuar
    projecao = None
    hoje = datetime.now().date()
    if mes == hoje.strftime("%Y-%m"):
        prev = ia.projecao_kwh_mes(db, unidade.id, mes, hoje)
        liberadas = [s for s in sessoes if s.status_cobranca in (m.COBRANCA_LIBERADA, m.COBRANCA_FATURADA)]
        base = [
            SessaoRateio(sessao_id=s.id, kwh=s.kwh_entregue, minutos_ociosos=s.minutos_ociosos, ajuste_valor=s.ajuste_valor)
            for s in liberadas
        ]
        estimada = SessaoRateio(sessao_id=0, kwh=prev["kwh_previsto_restante"])
        r = calcular_rateio(base + [estimada], parametros_calculo(obter_parametros(db)))
        projecao = {
            **prev,
            "kwh_projetado_mes": round(float(r.kwh_total), 2),
            "valor_projetado_mes": float(r.valor_total),
            "metodo": "ritmo do mês combinado com a média das últimas 8 semanas",
        }

    faturas = db.scalars(
        select(m.Fatura).where(m.Fatura.unidade_id == unidade.id).order_by(m.Fatura.mes_referencia.desc())
    )
    nao_lidas = db.scalar(
        select(func.count(m.Notificacao.id)).where(
            m.Notificacao.usuario_id == usuario.id, m.Notificacao.lida.is_(False)
        )
    )
    return {
        "mes": mes,
        "usuario": {"id": usuario.id, "nome": usuario.nome, "email": usuario.email},
        "unidade": {"id": unidade.id, "rotulo": unidade.rotulo},
        "veiculos": [
            {"placa": v.placa, "modelo": v.modelo, "bateria_kwh": v.capacidade_bateria_kwh}
            for vu in unidade.usuarios for v in vu.veiculos
        ],
        "fatura_parcial": parcial,
        "projecao_ia": projecao,
        "sessoes": [sessao_dict(s) for s in sessoes],
        "faturas": [fatura_dict(f) for f in faturas],
        "notificacoes_nao_lidas": nao_lidas,
    }

"""Integração do módulo de IA com o fluxo da plataforma.

É aqui que a IA deixa de ser um componente isolado e passa a ter papel
estrutural:

- avaliar_sessao_encerrada(): chamada por encerrar_sessao() em TODA sessão.
  O resultado decide o status de cobrança: a sessão vai direto para o rateio
  ou fica retida em "revisao_ia" até o gestor decidir. Ou seja, nenhuma
  sessão anômala chega na fatura sem passar por uma pessoa.

- atualizar_alertas_capacidade(): usa a previsão de demanda para gerar
  alertas de capacidade no painel do gestor (ex.: sugerir segundo ponto).

- projecao_fatura(): mostra ao morador quanto a fatura do mês deve fechar.
"""
import calendar
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.database import PASTA_DADOS
from app.ia.anomalias import DetectorAnomalias, Motivo, aplicar_regras, features_da_sessao
from app.ia.previsao import PrevisorDemanda, analisar_capacidade, montar_base_demanda, projetar_kwh_restante

ARQ_DETECTOR = PASTA_DADOS / "modelo_anomalias.joblib"
ARQ_PREVISOR = PASTA_DADOS / "modelo_demanda.joblib"

_detector: DetectorAnomalias | None = None
_previsor: PrevisorDemanda | None = None

# status que entram no histórico "confiável" usado para treinar
STATUS_HISTORICO = (m.COBRANCA_LIBERADA, m.COBRANCA_FATURADA)

ACOES = {
    "kwh_acima_da_bateria": "Conferir a leitura do medidor e se o RFID foi usado em outro veículo antes de cobrar.",
    "potencia_acima_do_limite": "Abrir chamado de manutenção para verificar o medidor; cobrar só após confirmar a leitura.",
    "conectado_sem_carregar": "Verificar se houve falha no carregador ou no veículo; avaliar descartar a sessão.",
    "falha_equipamento": "Confirmar a falha e, se comprovada, conceder crédito (ajuste) ao usuário.",
    "acima_do_perfil_do_usuario": "Confirmar com o usuário se houve uso de outro veículo ou empréstimo do cartão.",
    "ociosidade_longa": "Avisar o usuário; a taxa de ociosidade já foi aplicada conforme a regra.",
    "padrao_atipico": "Verificar a sessão: o padrão não se parece com o histórico (possível defeito ou uso indevido).",
}


def detector() -> DetectorAnomalias:
    global _detector
    if _detector is None:
        _detector = DetectorAnomalias.carregar(ARQ_DETECTOR)
    return _detector


def previsor() -> PrevisorDemanda:
    global _previsor
    if _previsor is None:
        _previsor = PrevisorDemanda()
        if ARQ_PREVISOR.exists():
            import joblib

            _previsor.__dict__.update(joblib.load(ARQ_PREVISOR))
    return _previsor


def resetar_modelos() -> None:
    """Esquece os modelos em memória e em disco (usado pelo simulador e testes)."""
    global _detector, _previsor
    _detector, _previsor = DetectorAnomalias(), PrevisorDemanda()
    for arq in (ARQ_DETECTOR, ARQ_PREVISOR):
        Path(arq).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Detecção de anomalias no encerramento da sessão
# --------------------------------------------------------------------------
@dataclass
class Avaliacao:
    score: float | None  # percentil do Isolation Forest (None se modelo ainda não treinado)
    motivos: list[dict] = field(default_factory=list)
    reter_para_revisao: bool = False
    severidade: str = "nenhuma"


def _feats(s: m.SessaoRecarga) -> dict:
    return features_da_sessao(s.duracao_min, s.kwh_entregue, s.potencia_media_kw, s.potencia_max_kw, s.minutos_ociosos)


def avaliar_sessao_encerrada(db: Session, sessao: m.SessaoRecarga) -> Avaliacao:
    feats = _feats(sessao)
    carregador = db.get(m.Carregador, sessao.carregador_id)
    veiculo = db.get(m.Veiculo, sessao.veiculo_id) if sessao.veiculo_id else None
    historico = list(
        db.scalars(
            select(m.SessaoRecarga.kwh_entregue).where(
                m.SessaoRecarga.usuario_id == sessao.usuario_id,
                m.SessaoRecarga.id != sessao.id,
                m.SessaoRecarga.status_cobranca.in_(STATUS_HISTORICO),
            )
        )
    )

    motivos: list[Motivo] = aplicar_regras(
        feats,
        sessao.status_final,
        veiculo.capacidade_bateria_kwh if veiculo else None,
        carregador.potencia_max_kw,
        historico,
    )

    resultado_modelo = detector().avaliar(feats)
    score = None
    if resultado_modelo is not None:
        score = resultado_modelo.percentil
        # o modelo só acrescenta motivo se nenhuma regra já explicou a sessão
        if resultado_modelo.outlier and not any(mo.severidade in ("media", "alta") for mo in motivos):
            detalhe = "; ".join(resultado_modelo.desvios) or "combinação incomum de duração, energia e potência"
            motivos.append(Motivo(
                "padrao_atipico",
                f"Isolation Forest: sessão mais atípica que {resultado_modelo.percentil:.0%} do histórico ({detalhe}).",
                "media", "modelo",
            ))

    ordem = {"nenhuma": 0, "baixa": 1, "media": 2, "alta": 3}
    severidade = max((mo.severidade for mo in motivos), key=ordem.get, default="nenhuma")
    return Avaliacao(
        score=score,
        motivos=[mo.como_dict() for mo in motivos],
        reter_para_revisao=ordem[severidade] >= ordem["media"],
        severidade=severidade,
    )


def alerta_da_avaliacao(sessao: m.SessaoRecarga, av: Avaliacao, quando: datetime) -> m.AlertaIA:
    principal = max(av.motivos, key=lambda mo: {"baixa": 1, "media": 2, "alta": 3}[mo["severidade"]])
    retida = " Cobrança retida até revisão." if av.reter_para_revisao else ""
    return m.AlertaIA(
        criado_em=quando,
        tipo="anomalia_sessao",
        severidade=av.severidade,
        sessao_id=sessao.id,
        titulo=f"Sessão #{sessao.id}: {principal['descricao'].split('.')[0]}",
        mensagem=" ".join(mo["descricao"] for mo in av.motivos) + retida,
        acao_sugerida=ACOES.get(principal["codigo"], "Analisar a sessão."),
        status="aberto",
    )


# --------------------------------------------------------------------------
# Treino dos modelos
# --------------------------------------------------------------------------
def base_sessoes(db: Session) -> pd.DataFrame:
    linhas = db.execute(
        select(
            m.SessaoRecarga.id,
            m.SessaoRecarga.duracao_min,
            m.SessaoRecarga.kwh_entregue,
            m.SessaoRecarga.potencia_media_kw,
            m.SessaoRecarga.potencia_max_kw,
            m.SessaoRecarga.minutos_ociosos,
        ).where(m.SessaoRecarga.status_cobranca.in_(STATUS_HISTORICO))
    ).all()
    return pd.DataFrame(
        [{"sessao_id": r[0], **features_da_sessao(*r[1:])} for r in linhas]
    )


def chegadas(db: Session) -> list[datetime]:
    sessoes = db.scalars(select(m.SessaoRecarga.inicio_em)).all()
    tentativas = db.scalars(select(m.TentativaOcupado.timestamp)).all()
    return list(sessoes) + list(tentativas)


def treinar_modelos(db: Session, agora: datetime | None = None) -> dict:
    """Retreina os dois modelos com o histórico atual e salva em disco."""
    import joblib

    agora = agora or datetime.now()
    det = DetectorAnomalias()
    info_det = det.treinar(base_sessoes(db))
    if det.treinado:
        det.salvar(ARQ_DETECTOR)
        global _detector
        _detector = det

    prev = PrevisorDemanda()
    info_prev = prev.treinar(montar_base_demanda(chegadas(db), agora))
    if prev.treinado:
        joblib.dump(prev.__dict__, ARQ_PREVISOR)
        global _previsor
        _previsor = prev
    return {"anomalias": info_det, "demanda": info_prev, "treinado_em": agora.isoformat(timespec="seconds")}


# --------------------------------------------------------------------------
# Previsão de capacidade (painel do gestor)
# --------------------------------------------------------------------------
def duracao_media_recente_h(db: Session, agora: datetime, semanas: int = 8) -> float:
    media_min = db.scalar(
        select(func.avg(m.SessaoRecarga.duracao_min)).where(
            m.SessaoRecarga.inicio_em >= agora - timedelta(weeks=semanas),
            m.SessaoRecarga.status_final != m.STATUS_EM_ANDAMENTO,
        )
    )
    return (media_min or 180) / 60


def previsao_capacidade(db: Session, agora: datetime | None = None, horizonte_semanas: int = 12) -> dict:
    agora = agora or datetime.now()
    prev = previsor()
    if not prev.treinado:
        return {"disponivel": False, "motivo": "modelo de demanda ainda não treinado"}
    n_carregadores = db.scalar(select(func.count(m.Carregador.id))) or 1
    duracao_h = duracao_media_recente_h(db, agora)
    tabela = prev.prever(horizonte_semanas)
    faixas = analisar_capacidade(tabela, duracao_h, n_carregadores)

    # grade dia x hora da próxima semana para o mapa de calor
    prox = tabela[tabela["semanas_a_frente"] == 1]
    grade = [
        [round(float(v), 3) for v in prox[prox["dia"] == d].sort_values("hora")["chegadas_previstas"]]
        for d in range(7)
    ]
    return {
        "disponivel": True,
        "horizonte_semanas": horizonte_semanas,
        "duracao_media_conectado_h": round(duracao_h, 2),
        "carregadores": n_carregadores,
        "metricas_modelo": prev.metricas,
        "faixas": faixas,
        "grade_proxima_semana": grade,
    }


def atualizar_alertas_capacidade(db: Session, agora: datetime | None = None) -> list[m.AlertaIA]:
    agora = agora or datetime.now()
    analise = previsao_capacidade(db, agora)
    if not analise["disponivel"]:
        return []
    # substitui os alertas de capacidade anteriores pela análise mais recente
    for antigo in db.scalars(select(m.AlertaIA).where(m.AlertaIA.tipo == "capacidade", m.AlertaIA.status == "aberto")):
        antigo.status = "resolvido"
        antigo.resolucao = "substituído por previsão mais recente"

    novos = []
    for f in analise["faixas"]:
        if f["status"] == "ok":
            continue
        quando = (
            "já está acima da capacidade"
            if f["status"] == "acima_capacidade"
            else f"deve passar da capacidade em ~{f['semanas_ate_exceder']} semanas"
            if f["semanas_ate_exceder"]
            else "está próxima do limite"
        )
        alerta = m.AlertaIA(
            criado_em=agora,
            tipo="capacidade",
            severidade="alta" if f["status"] == "acima_capacidade" else "media",
            titulo=f"{f['dia_nome'].capitalize()}, {f['faixa_nome']}: demanda {quando}",
            mensagem=(
                f"Ocupação prevista para a próxima semana: {f['ocupacao_proxima_semana']:.0%}; "
                f"em {analise['horizonte_semanas']} semanas: {f['ocupacao_fim_horizonte']:.0%}. "
                f"Crescimento estimado da demanda: {analise['metricas_modelo'].get('crescimento_semanal_pct', 0)}% por semana."
            ),
            acao_sugerida=(
                "Levar à assembleia/gestão a instalação de um segundo ponto ou criar regra de "
                "agendamento e tempo máximo nesse horário."
            ),
        )
        db.add(alerta)
        novos.append(alerta)
    db.commit()
    return novos


# --------------------------------------------------------------------------
# Projeção da fatura do mês (área do usuário)
# --------------------------------------------------------------------------
def projecao_kwh_mes(db: Session, unidade_id: int, mes: str, hoje: date) -> dict:
    ano, mm = (int(x) for x in mes.split("-"))
    ultimo_dia = date(ano, mm, calendar.monthrange(ano, mm)[1])
    inicio_janela = hoje - timedelta(weeks=8)

    linhas = db.execute(
        select(m.SessaoRecarga.inicio_em, m.SessaoRecarga.kwh_entregue).where(
            m.SessaoRecarga.unidade_id == unidade_id,
            m.SessaoRecarga.inicio_em >= datetime.combine(inicio_janela, datetime.min.time()),
            m.SessaoRecarga.inicio_em < datetime.combine(hoje + timedelta(days=1), datetime.min.time()),
            m.SessaoRecarga.status_cobranca != m.COBRANCA_DESCARTADA,
        )
    ).all()
    dias = pd.date_range(inicio_janela, hoje, freq="D").date
    serie = pd.Series(0.0, index=dias)
    for inicio, kwh in linhas:
        serie[inicio.date()] += kwh

    dias_restantes = [hoje + timedelta(days=i) for i in range(1, (ultimo_dia - hoje).days + 1)]
    if hoje.strftime("%Y-%m") != mes:
        dias_restantes = []  # mês já fechado
    return {
        "kwh_previsto_restante": round(projetar_kwh_restante(serie, dias_restantes), 2),
        "dias_restantes": len(dias_restantes),
        "media_diaria_8_semanas": round(float(serie.mean()), 2) if len(serie) else 0.0,
    }


def motivos_da_sessao(sessao: m.SessaoRecarga) -> list[dict]:
    return json.loads(sessao.anomalia_motivos) if sessao.anomalia_motivos else []

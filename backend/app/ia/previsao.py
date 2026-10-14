"""Previsão de demanda e de consumo (Abordagem 1 da Sprint 01).

Duas previsões, ambas com modelos interpretáveis (como decidido na Sprint 01:
"começando com modelos interpretáveis"):

1. Demanda pelo carregador (para o gestor): quantas chegadas (sessões +
   tentativas com carregador ocupado) são esperadas em cada dia da semana e
   hora, nas próximas semanas. Usamos Regressão de Poisson, adequada para
   contagens (0, 1, 2... chegadas por hora). O modelo é:

       log(chegadas esperadas) = efeito(dia, faixa do dia) + efeito(hora) + tendência × semana

   O termo de tendência captura o crescimento da frota elétrica e permite
   responder "em quantas semanas a demanda das terças à noite passa da
   capacidade do carregador?".

2. Projeção da fatura do mês (para o morador): kWh já consumido + estimativa
   para os dias que faltam. A estimativa diária mistura o ritmo do mês atual
   com a média das últimas 8 semanas, dando mais peso ao mês atual quanto mais
   dias dele já passaram. No backtest (notebook) essa mistura errou menos que
   usar só o histórico ou só a regra de três.
"""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

FAIXAS = {"madrugada": (0, 6), "manha": (6, 12), "tarde": (12, 18), "noite": (18, 24)}
NOMES_FAIXAS = {"madrugada": "madrugada (0h-6h)", "manha": "manhã (6h-12h)",
                "tarde": "tarde (12h-18h)", "noite": "noite (18h-24h)"}
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
HORAS_POR_FAIXA = 6
SEMANAS_BACKTEST = 4


def faixa_da_hora(hora: int) -> str:
    for nome, (ini, fim) in FAIXAS.items():
        if ini <= hora < fim:
            return nome
    raise ValueError(hora)


def segunda_da_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def montar_base_demanda(chegadas: list[datetime], ate: datetime) -> pd.DataFrame:
    """Uma linha por (semana, dia, hora) com o número de chegadas.

    Só entram semanas completas (a semana corrente, ainda em andamento,
    puxaria a média para baixo).
    """
    if not chegadas:
        return pd.DataFrame(columns=["semana", "dia", "hora", "chegadas"])
    primeira = segunda_da_semana(min(chegadas).date())
    ultima_completa = segunda_da_semana(ate.date()) - timedelta(days=7)
    n_semanas = (ultima_completa - primeira).days // 7 + 1
    if n_semanas <= 0:
        return pd.DataFrame(columns=["semana", "dia", "hora", "chegadas"])

    grade = np.zeros((n_semanas, 7, 24), dtype=int)
    for ts in chegadas:
        semana = (segunda_da_semana(ts.date()) - primeira).days // 7
        if 0 <= semana < n_semanas:
            grade[semana, ts.weekday(), ts.hour] += 1

    s, d, h = np.meshgrid(range(n_semanas), range(7), range(24), indexing="ij")
    df = pd.DataFrame({"semana": s.ravel(), "dia": d.ravel(), "hora": h.ravel(), "chegadas": grade.ravel()})
    df.attrs["primeira_segunda"] = primeira
    return df


def _matriz(df: pd.DataFrame) -> pd.DataFrame:
    X = {}
    faixa = df["hora"].map(faixa_da_hora)
    for d in range(7):
        for f in FAIXAS:
            X[f"{DIAS[d]}_{f}"] = ((df["dia"] == d) & (faixa == f)).astype(float)
    for h in range(24):
        X[f"h{h:02d}"] = (df["hora"] == h).astype(float)
    X["tendencia_semana"] = df["semana"].astype(float) / 52  # escala em anos
    return pd.DataFrame(X)


class PrevisorDemanda:
    def __init__(self):
        self.modelo: PoissonRegressor | None = None
        self.n_semanas = 0
        self.primeira_segunda: date | None = None
        self.metricas: dict = {}

    @property
    def treinado(self) -> bool:
        return self.modelo is not None

    @staticmethod
    def _novo_modelo() -> PoissonRegressor:
        return PoissonRegressor(alpha=1e-3, max_iter=2000)

    def treinar(self, df: pd.DataFrame) -> dict:
        n_semanas = int(df["semana"].max()) + 1 if len(df) else 0
        if n_semanas < SEMANAS_BACKTEST + 4:
            return {"treinado": False, "motivo": f"apenas {n_semanas} semanas completas de histórico"}

        # backtest: treina sem as últimas 4 semanas e compara com o que aconteceu
        corte = n_semanas - SEMANAS_BACKTEST
        treino, teste = df[df["semana"] < corte], df[df["semana"] >= corte]
        semanal_real = teste.groupby("semana")["chegadas"].sum().to_numpy()
        if (semanal_real == 0).any():
            # base rala demais (ex.: poucas sessões em 8 semanas): o erro semanal em % nem existe
            return {"treinado": False, "motivo": "semana sem nenhuma chegada no período de teste"}
        modelo_bt = self._novo_modelo().fit(_matriz(treino), treino["chegadas"])
        prev = modelo_bt.predict(_matriz(teste))
        # baseline ingênuo: média histórica do mesmo dia/hora (sem tendência)
        base = treino.groupby(["dia", "hora"])["chegadas"].mean()
        prev_base = teste.set_index(["dia", "hora"]).index.map(base).to_numpy()
        semanal_prev = pd.Series(prev, index=teste.index).groupby(teste["semana"]).sum().to_numpy()
        semanal_base = pd.Series(prev_base, index=teste.index).groupby(teste["semana"]).sum().to_numpy()
        self.metricas = {
            "semanas_historico": n_semanas,
            "semanas_backtest": SEMANAS_BACKTEST,
            "mae_por_hora_modelo": round(float(np.mean(np.abs(prev - teste["chegadas"]))), 4),
            "mae_por_hora_baseline": round(float(np.mean(np.abs(prev_base - teste["chegadas"]))), 4),
            "erro_semanal_medio_pct_modelo": round(float(np.mean(np.abs(semanal_prev - semanal_real) / semanal_real) * 100), 1),
            "erro_semanal_medio_pct_baseline": round(float(np.mean(np.abs(semanal_base - semanal_real) / semanal_real) * 100), 1),
        }

        # modelo final com todo o histórico
        self.modelo = self._novo_modelo().fit(_matriz(df), df["chegadas"])
        self.n_semanas = n_semanas
        self.primeira_segunda = df.attrs.get("primeira_segunda")
        coef = dict(zip(_matriz(df.head(1)).columns, self.modelo.coef_))
        self.metricas["crescimento_semanal_pct"] = round(float(np.exp(coef["tendencia_semana"] / 52) - 1) * 100, 2)
        return {"treinado": True, **self.metricas}

    def prever(self, semanas_a_frente: int) -> pd.DataFrame:
        """Chegadas esperadas para as próximas semanas (semana 1 = próxima semana completa)."""
        futuras = range(self.n_semanas, self.n_semanas + semanas_a_frente + 1)  # +1: inclui a semana corrente
        s, d, h = np.meshgrid(list(futuras), range(7), range(24), indexing="ij")
        df = pd.DataFrame({"semana": s.ravel(), "dia": d.ravel(), "hora": h.ravel()})
        df["chegadas_previstas"] = self.modelo.predict(_matriz(df))
        df["semanas_a_frente"] = df["semana"] - self.n_semanas
        return df


def analisar_capacidade(previsao: pd.DataFrame, duracao_media_h: float, n_carregadores: int) -> list[dict]:
    """Converte chegadas previstas em ocupação de cada faixa do dia.

    Ocupação ≈ (chegadas na faixa × duração média conectada) / (6 h × nº de carregadores).
    É uma aproximação simples (ignora sessões que atravessam a faixa), mas
    suficiente para apontar onde a fila vai aparecer primeiro.
    """
    df = previsao.copy()
    df["faixa"] = df["hora"].map(faixa_da_hora)
    por_faixa = df.groupby(["semanas_a_frente", "dia", "faixa"])["chegadas_previstas"].sum().reset_index()
    capacidade_h = HORAS_POR_FAIXA * n_carregadores
    por_faixa["ocupacao"] = por_faixa["chegadas_previstas"] * duracao_media_h / capacidade_h

    resultado = []
    for (dia, faixa), grupo in por_faixa.groupby(["dia", "faixa"]):
        grupo = grupo.sort_values("semanas_a_frente")
        atual = float(grupo[grupo["semanas_a_frente"] == 1]["ocupacao"].iloc[0])
        final = float(grupo["ocupacao"].iloc[-1])
        acima = grupo[(grupo["semanas_a_frente"] >= 1) & (grupo["ocupacao"] >= 1.0)]
        semanas = int(acima["semanas_a_frente"].iloc[0]) if len(acima) else None
        status = "acima_capacidade" if atual >= 1.0 else ("atencao" if atual >= 0.7 or semanas else "ok")
        resultado.append({
            "dia": int(dia),
            "dia_nome": DIAS[int(dia)],
            "faixa": faixa,
            "faixa_nome": NOMES_FAIXAS[faixa],
            "chegadas_previstas_proxima_semana": round(float(grupo[grupo["semanas_a_frente"] == 1]["chegadas_previstas"].iloc[0]), 2),
            "ocupacao_proxima_semana": round(atual, 3),
            "ocupacao_fim_horizonte": round(final, 3),
            "semanas_ate_exceder": semanas,
            "status": status,
        })
    return sorted(resultado, key=lambda r: -r["ocupacao_fim_horizonte"])


def projetar_kwh_restante(media_diaria_8_semanas: float, kwh_no_mes: float, dias_decorridos: int,
                          dias_no_mes: int, dias_restantes: int) -> float:
    """kWh esperados até o fim do mês.

    peso = fração do mês que já passou (dia 5 de 30 -> 0,17; dia 25 -> 0,83)
    consumo diário esperado = peso × ritmo do mês + (1 - peso) × média das 8 semanas
    """
    if dias_restantes <= 0 or dias_decorridos <= 0:
        return 0.0
    peso = dias_decorridos / dias_no_mes
    ritmo_do_mes = kwh_no_mes / dias_decorridos
    return dias_restantes * (peso * ritmo_do_mes + (1 - peso) * media_diaria_8_semanas)

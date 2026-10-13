"""Detecção de anomalias em sessões de recarga (Abordagem 2 da Sprint 01).

Combinamos duas camadas, como planejado ("regras estatísticas, Isolation Forest
ou comparação com perfil histórico"):

1. Regras de negócio explícitas: pegam casos que sabemos descrever
   (kWh maior que a bateria, potência acima do limite do carregador, vaga
   ocupada depois da carga completa...). São fáceis de auditar.

2. Isolation Forest (scikit-learn): aprende o padrão "normal" das sessões já
   registradas e aponta as que fogem dele em combinações que não previmos
   numa regra (ex.: potência média muito baixa para a duração, que pode
   indicar cabo ou carregador com defeito).

O Isolation Forest isola pontos com cortes aleatórios: pontos estranhos ficam
isolados com poucos cortes. Quanto mais fácil isolar, mais anômala a sessão.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# As variáveis são relativas ao veículo sempre que possível: 3 kW é normal
# para um híbrido plug-in, mas é sinal de problema num carro que carrega a 7 kW.
FEATURES = [
    "duracao_h",
    "kwh",
    "fracao_bateria",  # kWh entregues / capacidade da bateria
    "razao_potencia",  # potência média / potência esperada para o veículo
    "potencia_max_kw",
    "ociosas_h",
    "fracao_ociosa",
]

NOMES_FEATURES = {
    "duracao_h": ("duração", "h"),
    "kwh": ("energia", "kWh"),
    "fracao_bateria": ("fração da bateria carregada", ""),
    "razao_potencia": ("potência média / esperada", ""),
    "potencia_max_kw": ("potência máxima", "kW"),
    "ociosas_h": ("tempo ocioso", "h"),
    "fracao_ociosa": ("fração do tempo ociosa", ""),
}

MIN_SESSOES_TREINO = 40  # abaixo disso o modelo não é confiável -> só regras
PERCENTIL_CORTE = 0.97  # sessão mais atípica que 97% do histórico


def br(valor: float, casas: int = 1) -> str:
    """Número com vírgula decimal: 47.06 -> '47,1'."""
    return f"{valor:.{casas}f}".replace(".", ",")


def features_da_sessao(duracao_min: float, kwh: float, potencia_media_kw: float,
                       potencia_max_kw: float, minutos_ociosos: float,
                       potencia_esperada_kw: float, bateria_kwh: float) -> dict:
    duracao_h = max(duracao_min / 60, 1 / 60)
    ociosas_h = minutos_ociosos / 60
    return {
        "duracao_h": duracao_h,
        "kwh": kwh,
        "potencia_media_kw": potencia_media_kw,
        "fracao_bateria": kwh / bateria_kwh if bateria_kwh else 0.0,
        "razao_potencia": min(potencia_media_kw / potencia_esperada_kw, 3.0) if potencia_esperada_kw else 1.0,
        "potencia_max_kw": potencia_max_kw,
        "ociosas_h": ociosas_h,
        "fracao_ociosa": min(ociosas_h / duracao_h, 1.0),
    }


@dataclass
class Motivo:
    codigo: str
    descricao: str
    severidade: str  # baixa | media | alta
    origem: str  # regra | modelo

    def como_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ResultadoModelo:
    percentil: float  # 0..1, quão atípica em relação ao histórico
    outlier: bool
    desvios: list[str] = field(default_factory=list)


class DetectorAnomalias:
    def __init__(self):
        self.pipeline = None
        self.scores_treino: np.ndarray | None = None
        self.media: pd.Series | None = None
        self.desvio: pd.Series | None = None
        self.n_treino = 0
        self.treinado_em: datetime | None = None

    @property
    def treinado(self) -> bool:
        return self.pipeline is not None

    def treinar(self, df: pd.DataFrame) -> dict:
        if len(df) < MIN_SESSOES_TREINO:
            return {"treinado": False, "motivo": f"histórico com {len(df)} sessões (mínimo {MIN_SESSOES_TREINO})"}
        X = df[FEATURES].astype(float)
        self.pipeline = make_pipeline(
            StandardScaler(),
            IsolationForest(n_estimators=200, contamination="auto", random_state=42),
        )
        self.pipeline.fit(X)
        # decision_function: positivo = normal, negativo = anômalo. Invertemos
        # o sinal para "quanto maior, mais anômala" e guardamos a distribuição
        # do treino para converter qualquer score em percentil.
        self.scores_treino = np.sort(-self.pipeline.decision_function(X))
        self.media = X.mean()
        self.desvio = X.std().replace(0, 1)
        self.n_treino = len(X)
        self.treinado_em = datetime.now()
        outliers = float((self.scores_treino > 0).mean())
        return {"treinado": True, "sessoes": self.n_treino, "fracao_outliers_treino": round(outliers, 4)}

    def avaliar(self, feats: dict) -> ResultadoModelo | None:
        if not self.treinado:
            return None
        X = pd.DataFrame([feats])[FEATURES].astype(float)
        score = float(-self.pipeline.decision_function(X)[0])
        percentil = float(np.searchsorted(self.scores_treino, score) / len(self.scores_treino))
        outlier = score > 0 and percentil >= PERCENTIL_CORTE

        # explica o resultado: quais variáveis mais se afastaram da média
        z = ((X.iloc[0] - self.media) / self.desvio).abs().sort_values(ascending=False)
        desvios = []
        for nome in z.index[:2]:
            if z[nome] < 1.5:
                break
            rotulo, unidade = NOMES_FEATURES[nome]
            casas = 1 if unidade else 2
            sufixo = f" {unidade}" if unidade else ""
            desvios.append(
                f"{rotulo} {br(X.iloc[0][nome], casas)}{sufixo} (típico: {br(self.media[nome], casas)}{sufixo})"
            )
        return ResultadoModelo(percentil=round(percentil, 4), outlier=outlier, desvios=desvios)

    def salvar(self, caminho: Path) -> None:
        joblib.dump(self.__dict__, caminho)

    @classmethod
    def carregar(cls, caminho: Path) -> "DetectorAnomalias":
        det = cls()
        if caminho.exists():
            det.__dict__.update(joblib.load(caminho))
        return det


def aplicar_regras(
    feats: dict,
    status_final: str,
    capacidade_bateria_kwh: float | None,
    potencia_max_carregador_kw: float,
    historico_kwh_usuario: list[float],
) -> list[Motivo]:
    """Regras de negócio explícitas. Cada regra devolve um motivo legível."""
    motivos: list[Motivo] = []
    kwh = feats["kwh"]

    if capacidade_bateria_kwh and kwh > capacidade_bateria_kwh * 1.05:
        motivos.append(Motivo(
            "kwh_acima_da_bateria",
            f"{br(kwh, 1)} kWh entregues, mais que a bateria do veículo ({br(capacidade_bateria_kwh, 1)} kWh). "
            "Possível erro de medição ou RFID usado em outro veículo.",
            "alta", "regra",
        ))

    if feats["potencia_max_kw"] > potencia_max_carregador_kw * 1.10:
        motivos.append(Motivo(
            "potencia_acima_do_limite",
            f"Pico de {br(feats['potencia_max_kw'], 1)} kW, acima do limite do carregador "
            f"({br(potencia_max_carregador_kw, 1)} kW). Verificar medidor/telemetria.",
            "alta", "regra",
        ))

    if feats["duracao_h"] >= 1 and kwh < 0.5:
        motivos.append(Motivo(
            "conectado_sem_carregar",
            f"{br(feats['duracao_h'], 1)} h conectado e só {br(kwh, 2)} kWh entregues.",
            "media", "regra",
        ))

    if status_final == "falha":
        motivos.append(Motivo(
            "falha_equipamento",
            "Sessão encerrada por falha do carregador ou de comunicação.",
            "media", "regra",
        ))

    if len(historico_kwh_usuario) >= 8:
        media, desvio = float(np.mean(historico_kwh_usuario)), float(np.std(historico_kwh_usuario))
        if kwh > 5 and kwh > media + 3 * max(desvio, 1.0) and kwh > 1.5 * media:
            motivos.append(Motivo(
                "acima_do_perfil_do_usuario",
                f"{br(kwh, 1)} kWh, bem acima do perfil do usuário (média {br(media, 1)} kWh).",
                "media", "regra",
            ))

    if feats["ociosas_h"] >= 3:
        # baixa: gera alerta para avisar o usuário, mas não retém a cobrança,
        # porque a taxa de ociosidade já é aplicada automaticamente pelo rateio
        motivos.append(Motivo(
            "ociosidade_longa",
            f"Veículo ocupou a vaga por {br(feats['ociosas_h'], 1)} h depois da carga completa.",
            "baixa", "regra",
        ))
    return motivos

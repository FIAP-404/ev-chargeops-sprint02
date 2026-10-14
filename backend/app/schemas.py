"""Formatos de entrada da API (validados pelo Pydantic)."""
from datetime import datetime

from pydantic import BaseModel, Field


class IniciarSessao(BaseModel):
    carregador_id: str = "goodwe_hca_g2_l1"
    codigo_rfid: str = Field(..., examples=["RFID-A1204-01"])
    inicio_em: datetime | None = None
    leitura_inicial_kwh: float | None = Field(
        None, ge=0, description="leitura do medidor no início; vazio = última leitura do carregador"
    )


class Telemetria(BaseModel):
    timestamp: datetime
    potencia_kw: float = Field(..., ge=0)
    energia_acumulada_kwh: float = Field(..., ge=0)
    status_carregador: str = Field("carregando", pattern="^(carregando|carga_completa|erro|ocioso)$")


class EncerrarSessao(BaseModel):
    motivo: str = Field("usuario_desconectou", examples=["usuario_desconectou"])
    fim_em: datetime | None = None
    leitura_final_kwh: float | None = Field(None, description="vazio = usar última telemetria válida")


class RevisaoSessao(BaseModel):
    decisao: str = Field(..., pattern="^(liberar|descartar)$")
    usuario_id: int | None = None
    ajuste_valor: float = Field(0.0, ge=0)
    observacao: str = ""


class Parametros(BaseModel):
    tarifa_kwh: float = Field(..., gt=0)
    perdas_percentual: float = Field(..., ge=0, le=50)
    taxa_infra: float = Field(..., ge=0)
    cobrar_infra_sem_uso: bool
    ociosidade_ativa: bool
    tolerancia_ociosidade_min: float = Field(..., ge=0)
    penalidade_ociosidade_hora: float = Field(..., ge=0)


class ResolverAlerta(BaseModel):
    resolucao: str = "tratado pelo gestor"

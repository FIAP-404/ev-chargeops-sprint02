"""Adaptador para o GoodWe SEMS Portal (camada de conectividade da arquitetura).

SITUAÇÃO NA SPRINT 02: NÃO VALIDADO COM DADOS REAIS DE SESSÃO.
Na Sprint 01 confirmamos, com acesso autorizado, que a planta "LAB FIAP Eco
Smart Home" lista um equipamento "EV Charger", mas o retorno explorado não
trazia histórico de sessões (início/fim, kWh por usuário/RFID). Por isso o
protótipo usa o simulador como fonte de dados, e este módulo deixa pronto o
caminho de integração:

    SEMS (telemetria do HCA G2)  ->  normalizar_leitura()  ->  registrar_telemetria()

sincronizar_sems() faz esse caminho uma vez e é chamada pela rota
POST /integracoes/sems/sincronizar quando as credenciais estão configuradas.

A identificação do usuário continua vindo do RFID/app (iniciar_sessao), que é
o fallback "registro próprio" definido na Sprint 01.

Os endpoints (CrossLogin, GetMonitorDetailByPowerstationId) vêm de material
comunitário citado na Sprint 01, seção 4.4. Os nomes de campos do carregador
no retorno precisam ser confirmados com um retorno real; por isso a
normalização aceita uma lista de nomes candidatos para cada campo.

Credenciais nunca ficam no código: são lidas de variáveis de ambiente
(EVCO_SEMS_USUARIO, EVCO_SEMS_SENHA, EVCO_SEMS_PLANTA_ID).
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.sessoes import registrar_telemetria, sessao_aberta_do_carregador

URL_LOGIN = "https://www.semsportal.com/api/v2/Common/CrossLogin"
TOKEN_INICIAL = {"version": "v2.1.0", "client": "web", "language": "en"}

# nomes possíveis de cada campo no retorno do SEMS (a confirmar com dado real)
CAMPOS_POTENCIA = ("power", "pac", "charge_power", "chargePower", "output_power")
CAMPOS_ENERGIA = ("total_energy", "etotal", "eTotal", "charge_energy_total", "chargeEnergy")
CAMPOS_STATUS = ("status", "state", "work_mode", "chargeStatus")

# como traduzimos o status do carregador para o vocabulário da plataforma
MAPA_STATUS = {
    "charging": "carregando",
    "1": "carregando",
    "full": "carga_completa",
    "finished": "carga_completa",
    "2": "carga_completa",
    "fault": "erro",
    "error": "erro",
    "-1": "erro",
}


@dataclass
class LeituraCarregador:
    timestamp: datetime
    potencia_kw: float
    energia_acumulada_kwh: float
    status: str  # carregando | carga_completa | erro | ocioso


def _primeiro(dados: dict, candidatos: tuple[str, ...]):
    for chave in candidatos:
        if chave in dados and dados[chave] not in (None, ""):
            return dados[chave]
    return None


def localizar_ev_charger(detalhe_planta: dict) -> dict | None:
    """Procura o equipamento do tipo carregador dentro do retorno da planta."""
    candidatos = []
    data = detalhe_planta.get("data", detalhe_planta)
    for chave in ("inverter", "evcharger", "ev_charger", "evchargers", "equipments"):
        valor = data.get(chave)
        if isinstance(valor, list):
            candidatos.extend(valor)
    for equip in candidatos:
        texto = json.dumps(equip).lower()
        if "ev charger" in texto or "evcharger" in texto or "hca" in texto:
            return equip
    return None


def normalizar_leitura(equipamento: dict, agora: datetime | None = None) -> LeituraCarregador:
    """Converte o JSON de um equipamento do SEMS para o formato da plataforma."""
    detalhe = {**equipamento, **equipamento.get("d", {}), **equipamento.get("invert_full", {})}
    potencia = float(_primeiro(detalhe, CAMPOS_POTENCIA) or 0)
    if potencia > 100:  # alguns campos vêm em W, não em kW
        potencia /= 1000
    energia = _primeiro(detalhe, CAMPOS_ENERGIA)
    if energia is None:
        raise ValueError("Leitura do SEMS sem campo de energia acumulada reconhecido")
    status_bruto = str(_primeiro(detalhe, CAMPOS_STATUS) or "").lower()
    status = MAPA_STATUS.get(status_bruto, "carregando" if potencia > 0.2 else "ocioso")
    return LeituraCarregador(
        timestamp=agora or datetime.now(),
        potencia_kw=round(potencia, 3),
        energia_acumulada_kwh=float(energia),
        status=status,
    )


class ClienteSEMS:
    """Cliente mínimo do SEMS. Só é usado quando as variáveis de ambiente existem."""

    def __init__(self, usuario: str, senha: str, timeout: float = 15.0):
        self.usuario = usuario
        self.senha = senha
        self.http = httpx.Client(timeout=timeout)
        self.token: dict | None = None
        self.api_base: str | None = None

    @classmethod
    def do_ambiente(cls) -> "ClienteSEMS | None":
        usuario, senha = os.getenv("EVCO_SEMS_USUARIO"), os.getenv("EVCO_SEMS_SENHA")
        return cls(usuario, senha) if usuario and senha else None

    def login(self) -> None:
        resp = self.http.post(
            URL_LOGIN,
            headers={"Token": json.dumps(TOKEN_INICIAL)},
            json={"account": self.usuario, "pwd": self.senha},
        )
        resp.raise_for_status()
        corpo = resp.json()
        if not corpo.get("data"):
            raise RuntimeError(f"Login no SEMS recusado: {corpo.get('msg')}")
        self.token = corpo["data"]
        self.api_base = corpo.get("api", "https://www.semsportal.com/api/")

    def detalhe_planta(self, planta_id: str) -> dict:
        if self.token is None:
            self.login()
        resp = self.http.post(
            f"{self.api_base}v2/PowerStation/GetMonitorDetailByPowerstationId",
            headers={"Token": json.dumps(self.token)},
            json={"powerStationId": planta_id},
        )
        resp.raise_for_status()
        return resp.json()

    def leitura_carregador(self, planta_id: str) -> LeituraCarregador:
        equipamento = localizar_ev_charger(self.detalhe_planta(planta_id))
        if equipamento is None:
            raise RuntimeError("Nenhum EV Charger encontrado na planta")
        return normalizar_leitura(equipamento)


def sincronizar_sems(db: Session, cliente: ClienteSEMS, planta_id: str, carregador_id: str) -> dict:
    """Lê o carregador no SEMS e, se houver sessão aberta nele, grava a leitura como telemetria."""
    leitura = cliente.leitura_carregador(planta_id)
    sessao = sessao_aberta_do_carregador(db, carregador_id)
    if sessao is not None:
        registrar_telemetria(
            db, sessao.id, leitura.timestamp, leitura.potencia_kw, leitura.energia_acumulada_kwh, leitura.status
        )
    return {
        "sessao_id": sessao.id if sessao is not None else None,
        "potencia_kw": leitura.potencia_kw,
        "energia_acumulada_kwh": leitura.energia_acumulada_kwh,
        "status": leitura.status,
    }

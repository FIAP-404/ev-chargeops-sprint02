"""Adaptador OCPP 1.6J (lado Central System) para o ciclo da sessão.

É um SUBCONJUNTO do OCPP 1.6J, mapeado à mão (sem a biblioteca `ocpp`, que é
assíncrona e quer controlar a conexão), e NÃO FOI TESTADO EM HARDWARE: o HCA G2
do laboratório é acessado pelo SEMS e não tivemos carregador ou gateway OCPP
para validar. Assim como o simulador, a API e o adaptador do SEMS, ele só
traduz mensagens para as mesmas três funções de app.sessoes:

    StartTransaction  ->  iniciar_sessao()
    MeterValues / StatusNotification  ->  registrar_telemetria()
    StopTransaction  ->  encerrar_sessao()

Formato das mensagens (OCPP-J): o carregador manda CALL [2, id, ação, payload]
e recebe CALLRESULT [3, id, resposta] ou CALLERROR [4, id, código, descrição, {}].
O transactionId do OCPP é o id da sessão. Energia chega em Wh e potência em W.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.sessoes import (
    ErroSessao,
    encerrar_sessao,
    iniciar_sessao,
    para_hora_local,
    registrar_telemetria,
    sessao_aberta_do_carregador,
)

log = logging.getLogger("evchargeops.ocpp")

INTERVALO_HEARTBEAT_S = 60

# ações do OCPP 1.6 que o carregador pode mandar, mas que não tratamos
NAO_SUPORTADAS = {"DataTransfer", "DiagnosticsStatusNotification", "FirmwareStatusNotification"}

# reason do StopTransaction -> motivo de encerramento da plataforma
MOTIVOS_OCPP = {
    "EVDisconnected": "usuario_desconectou",
    "Local": "usuario_desconectou",
    "EmergencyStop": "falha_carregador",
    "PowerLoss": "falha_carregador",
    "Reboot": "falha_carregador",
}  # demais (Remote, HardReset, DeAuthorized, Other...) -> limite_operacional

# status do conector no StatusNotification -> status_carregador da telemetria
# (Finishing não entra: quer dizer que a transação parou, não que a bateria encheu)
STATUS_OCPP = {"SuspendedEV": "carga_completa", "Faulted": "erro"}

MEDIDA_ENERGIA = "Energy.Active.Import.Register"  # medida padrão quando o measurand vem vazio
MEDIDA_POTENCIA = "Power.Active.Import"


class ErroOCPP(Exception):
    """Vira um CALLERROR. codigo é sempre um dos códigos de erro da spec 1.6."""

    def __init__(self, codigo: str, descricao: str):
        super().__init__(descricao)
        self.codigo = codigo


def processar_texto(db: Session, carregador_id: str, texto: str) -> str | None:
    """Mesma coisa que processar(), mas com o texto cru do websocket."""
    try:
        frame = json.loads(texto)
    except (ValueError, RecursionError):  # JSON inválido ou aninhado demais
        frame = None  # vira FormationViolation
    resposta = processar(db, carregador_id, frame)
    return None if resposta is None else json.dumps(resposta, ensure_ascii=False)


def processar(db: Session, carregador_id: str, frame) -> list | None:
    """Responde um CALL do carregador. Devolve None para CALLRESULT/CALLERROR
    vindos dele, porque o Central System daqui não envia CALLs."""
    if isinstance(frame, list) and frame and frame[0] in (3, 4):
        return None
    id_mensagem = frame[1] if isinstance(frame, list) and len(frame) > 1 and isinstance(frame[1], str) else ""
    try:
        if not (
            isinstance(frame, list) and len(frame) == 4 and frame[0] == 2 and id_mensagem
            and isinstance(frame[2], str) and isinstance(frame[3], dict)
        ):
            raise ErroOCPP("FormationViolation", "Esperado [2, id, ação, payload]")
        acao, payload = frame[2], frame[3]
        if acao in NAO_SUPORTADAS:
            raise ErroOCPP("NotSupported", f"Ação {acao} não suportada por este Central System")
        tratar = ACOES.get(acao)
        if tratar is None:
            raise ErroOCPP("NotImplemented", f"Ação {acao} desconhecida")
        return [3, id_mensagem, tratar(db, carregador_id, payload)]
    except ErroOCPP as erro:
        return [4, id_mensagem, erro.codigo, str(erro), {}]
    except ErroSessao as erro:
        # regra de negócio recusou um valor (ex.: meterStart abaixo do fim da sessão anterior)
        return [4, id_mensagem, "PropertyConstraintViolation", str(erro), {}]
    except Exception:
        db.rollback()
        log.exception("Erro ao processar mensagem OCPP do carregador %s", carregador_id)
        return [4, id_mensagem, "InternalError", "Erro interno no Central System", {}]


# --- conversões ---------------------------------------------------------------

def _tipo(valor, tipo: type, nome: str):
    if not isinstance(valor, tipo):
        raise ErroOCPP("TypeConstraintViolation", f"Tipo inválido em {nome}: {valor!r}")
    return valor


def _campo(payload: dict, nome: str, tipo: type = object):
    if payload.get(nome) is None:
        raise ErroOCPP("OccurenceConstraintViolation", f"Campo obrigatório ausente: {nome}")
    return _tipo(payload[nome], tipo, nome)


def _data(valor) -> datetime:
    try:
        dt = datetime.fromisoformat(valor)
    except (TypeError, ValueError) as erro:
        raise ErroOCPP("TypeConstraintViolation", f"Data inválida: {valor!r}") from erro
    # no OCPP as datas são em UTC; sem fuso explícito, assumimos UTC
    return para_hora_local(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def _numero(valor) -> float:
    try:
        return float(valor)  # sampledValue.value chega como string
    except (TypeError, ValueError) as erro:
        raise ErroOCPP("TypeConstraintViolation", f"Número inválido: {valor!r}") from erro


def _kwh(valor, unidade: str = "Wh") -> float:
    v = _numero(valor)
    return v if unidade == "kWh" else v / 1000


def _kw(valor, unidade: str = "W") -> float:
    v = _numero(valor)
    return v if unidade == "kW" else v / 1000


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- ações --------------------------------------------------------------------

def _boot_notification(db: Session, carregador_id: str, payload: dict) -> dict:
    _campo(payload, "chargePointVendor", str)
    _campo(payload, "chargePointModel", str)
    conhecido = db.get(m.Carregador, carregador_id) is not None
    return {
        "status": "Accepted" if conhecido else "Rejected",
        "currentTime": _agora_iso(),
        "interval": INTERVALO_HEARTBEAT_S,
    }


def _heartbeat(db: Session, carregador_id: str, payload: dict) -> dict:
    return {"currentTime": _agora_iso()}


def _authorize(db: Session, carregador_id: str, payload: dict) -> dict:
    # tag desconhecida também é aceita: a sessão abre e vai para a quarentena (regra da Sprint 01)
    _campo(payload, "idTag", str)
    return {"idTagInfo": {"status": "Accepted"}}


def _start_transaction(db: Session, carregador_id: str, payload: dict) -> dict:
    _campo(payload, "connectorId", int)
    id_tag = _campo(payload, "idTag", str)
    leitura_inicial = _kwh(_campo(payload, "meterStart"))
    inicio_em = _data(_campo(payload, "timestamp", str))
    try:
        # o idTag é o cartão lido pelo carregador: o OCPP é só o transporte,
        # então a sessão fica com metodo_identificacao "rfid"
        sessao = iniciar_sessao(db, carregador_id, id_tag, inicio_em, leitura_inicial)
    except ErroSessao as erro:
        # a tentativa no carregador ocupado já foi gravada pelo domínio
        if erro.status_http == 409:
            return {"transactionId": 0, "idTagInfo": {"status": "ConcurrentTx"}}
        if erro.status_http == 404:
            return {"transactionId": 0, "idTagInfo": {"status": "Invalid"}}
        raise
    return {"transactionId": sessao.id, "idTagInfo": {"status": "Accepted"}}


def _sessao_da_transacao(db: Session, carregador_id: str, transacao) -> m.SessaoRecarga | None:
    """Sessão aberta deste carregador; sem transactionId, a que estiver aberta nele."""
    if transacao is None:
        return sessao_aberta_do_carregador(db, carregador_id)
    sessao = db.get(m.SessaoRecarga, int(_numero(transacao)))
    if sessao is None or sessao.carregador_id != carregador_id or sessao.status_final != m.STATUS_EM_ANDAMENTO:
        return None
    return sessao


def _meter_values(db: Session, carregador_id: str, payload: dict) -> dict:
    _campo(payload, "connectorId", int)
    leituras = _campo(payload, "meterValue", list)
    sessao = _sessao_da_transacao(db, carregador_id, payload.get("transactionId"))
    if sessao is None:
        return {}  # leitura fora de uma sessão aberta não tem onde ser gravada

    for leitura in leituras:
        _tipo(leitura, dict, "meterValue")
        quando = _data(_campo(leitura, "timestamp", str))
        energia, potencia = None, 0.0
        for amostra in _campo(leitura, "sampledValue", list):
            _tipo(amostra, dict, "sampledValue")
            if amostra.get("phase") is not None:
                continue  # valores por fase (L1, L2...) são parciais; usamos só o total
            medida = amostra.get("measurand", MEDIDA_ENERGIA)
            if medida == MEDIDA_ENERGIA:
                energia = _kwh(_campo(amostra, "value"), amostra.get("unit", "Wh"))
            elif medida == MEDIDA_POTENCIA:
                potencia = _kw(_campo(amostra, "value"), amostra.get("unit", "W"))
        if energia is None:
            continue  # sem o medidor não dá para calcular o kWh da sessão
        registrar_telemetria(db, sessao.id, quando, potencia, energia, status_carregador="carregando")
    return {}


def _ultimo_evento(db: Session, sessao: m.SessaoRecarga) -> datetime:
    return db.scalar(select(func.max(m.EventoSessao.timestamp)).where(m.EventoSessao.sessao_id == sessao.id))


def _status_notification(db: Session, carregador_id: str, payload: dict) -> dict:
    _campo(payload, "connectorId", int)
    _campo(payload, "errorCode", str)
    status = STATUS_OCPP.get(_campo(payload, "status", str))
    sessao = sessao_aberta_do_carregador(db, carregador_id)
    if status is None or sessao is None:
        return {}
    # o timestamp é opcional; sem ele usamos o último evento da sessão, e não o
    # relógio do servidor, que pode estar à frente do relógio do carregador
    quando = _data(payload["timestamp"]) if payload.get("timestamp") is not None else _ultimo_evento(db, sessao)
    # não traz leitura do medidor: repete a última para só marcar o novo status
    registrar_telemetria(db, sessao.id, quando, 0.0, sessao.leitura_final_kwh, status_carregador=status)
    return {}


def _stop_transaction(db: Session, carregador_id: str, payload: dict) -> dict:
    transacao = _campo(payload, "transactionId")
    leitura_final = _kwh(_campo(payload, "meterStop"))
    fim_em = _data(_campo(payload, "timestamp", str))
    motivo = MOTIVOS_OCPP.get(_tipo(payload.get("reason", "Local"), str, "reason"), "limite_operacional")
    sessao = _sessao_da_transacao(db, carregador_id, transacao)
    if sessao is None:
        # já encerrada ou inexistente (inclui transactionId 0): o carregador reenvia
        # o Stop até receber resposta, então confirmamos sem fazer nada
        return {}

    # Um Stop recusado deixaria a sessão aberta para sempre (o carregador desiste
    # depois de algumas tentativas). Então a sessão sempre fecha: hora antes da
    # última telemetria vira a hora dela, e medidor abaixo da última leitura cai
    # na reconciliação do domínio (usa o último dado válido).
    ultimo = _ultimo_evento(db, sessao)
    if fim_em < ultimo:
        log.warning("StopTransaction %s com hora anterior à última telemetria; usando %s", sessao.id, ultimo)
        fim_em = ultimo
    if leitura_final < sessao.leitura_final_kwh:
        log.warning("StopTransaction %s com meterStop abaixo da última leitura; reconciliando", sessao.id)
        leitura_final = None
    encerrar_sessao(db, sessao.id, motivo, fim_em, leitura_final)
    return {}


ACOES = {
    "BootNotification": _boot_notification,
    "Heartbeat": _heartbeat,
    "Authorize": _authorize,
    "StartTransaction": _start_transaction,
    "MeterValues": _meter_values,
    "StatusNotification": _status_notification,
    "StopTransaction": _stop_transaction,
}

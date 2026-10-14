"""Testes do adaptador OCPP 1.6J com mensagens montadas à mão (não é um carregador real)."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from app import models as m
from app.database import get_db
from app.integracoes.ocpp16 import processar, processar_texto
from app.main import app
from app.sessoes import para_hora_local

CARREGADOR = "goodwe_hca_g2_l1"
TAG = "RFID-A1204-01"


@pytest.fixture
def cliente(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def chamar(db, acao, payload, id_mensagem="1"):
    return processar(db, CARREGADOR, [2, id_mensagem, acao, payload])


def iniciar(db, tag=TAG, meter_start=1000000, quando="2026-08-03T19:10:00Z"):
    r = chamar(db, "StartTransaction", {"connectorId": 1, "idTag": tag, "meterStart": meter_start, "timestamp": quando})
    return r[2]["transactionId"]


def medir(db, transacao, quando, energia_wh, potencia_w=None):
    amostras = [{"value": str(energia_wh), "measurand": "Energy.Active.Import.Register", "unit": "Wh"}]
    if potencia_w is not None:
        amostras.append({"value": str(potencia_w), "measurand": "Power.Active.Import", "unit": "W"})
    return chamar(db, "MeterValues", {
        "connectorId": 1, "transactionId": transacao, "meterValue": [{"timestamp": quando, "sampledValue": amostras}],
    })


def parar(db, transacao, meter_stop, quando="2026-08-03T22:40:00Z", motivo="EVDisconnected"):
    payload = {"transactionId": transacao, "meterStop": meter_stop, "timestamp": quando}
    if motivo is not None:
        payload["reason"] = motivo
    return chamar(db, "StopTransaction", payload)


def test_ciclo_completo_ocpp(db):
    boot = chamar(db, "BootNotification", {"chargePointVendor": "GoodWe", "chargePointModel": "HCA G2"})
    assert boot[0] == 3 and boot[2]["status"] == "Accepted" and boot[2]["interval"] == 60

    transacao = iniciar(db)
    medir(db, transacao, "2026-08-03T20:40:00Z", 1009300, 6200)
    medir(db, transacao, "2026-08-03T22:10:00Z", 1018600, 6200)
    chamar(db, "StatusNotification", {
        "connectorId": 1, "errorCode": "NoError", "status": "SuspendedEV", "timestamp": "2026-08-03T22:10:00Z",
    })
    assert parar(db, transacao, 1018600) == [3, "1", {}]

    sessao = db.get(m.SessaoRecarga, transacao)
    assert sessao.kwh_entregue == pytest.approx(18.6)
    assert sessao.status_final == "concluida"
    assert sessao.status_cobranca == "liberada"
    assert sessao.metodo_identificacao == "rfid"
    assert sessao.minutos_ociosos == pytest.approx(30)


def utc(hora, minuto):
    return para_hora_local(datetime(2026, 8, 3, hora, minuto, tzinfo=timezone.utc))


def test_meter_values_converte_wh_e_w(db):
    transacao = iniciar(db)
    medir(db, transacao, "2026-08-03T19:40:00Z", 1003100, 6200)
    sessao = db.get(m.SessaoRecarga, transacao)
    assert sessao.leitura_inicial_kwh == pytest.approx(1000)
    assert sessao.leitura_final_kwh == pytest.approx(1003.1)
    assert sessao.potencia_max_kw == pytest.approx(6.2)

    # sem transactionId vale a sessão aberta; sem measurand é energia; aqui em kWh
    chamar(db, "MeterValues", {"connectorId": 1, "meterValue": [
        {"timestamp": "2026-08-03T20:10:00Z", "sampledValue": [{"value": "1006.2", "unit": "kWh"}]},
    ]})
    assert db.get(m.SessaoRecarga, transacao).leitura_final_kwh == pytest.approx(1006.2)


def test_meter_values_ignora_valores_por_fase(db):
    transacao = iniciar(db)
    chamar(db, "MeterValues", {"connectorId": 1, "transactionId": transacao, "meterValue": [{
        "timestamp": "2026-08-03T19:40:00Z",
        "sampledValue": [
            {"value": "1003100"},
            {"value": "6200", "measurand": "Power.Active.Import", "unit": "W"},
            {"value": "1001000", "phase": "L1"},
            {"value": "2100", "measurand": "Power.Active.Import", "unit": "W", "phase": "L1"},
        ],
    }]})
    sessao = db.get(m.SessaoRecarga, transacao)
    assert sessao.leitura_final_kwh == pytest.approx(1003.1)
    assert sessao.potencia_max_kw == pytest.approx(6.2)
    tipos = db.scalars(select(m.EventoSessao.tipo_evento).where(m.EventoSessao.sessao_id == transacao)).all()
    assert tipos == ["inicio", "telemetria"]


def test_start_com_carregador_ocupado_responde_concurrent_tx(db):
    iniciar(db)
    r = chamar(db, "StartTransaction", {
        "connectorId": 1, "idTag": "RFID-A0302-01", "meterStart": 1000000, "timestamp": "2026-08-03T19:30:00Z",
    })
    assert r == [3, "1", {"transactionId": 0, "idTagInfo": {"status": "ConcurrentTx"}}]
    assert db.scalar(select(func.count()).select_from(m.TentativaOcupado)) == 1


def test_tag_desconhecida_vai_para_quarentena(db):
    assert chamar(db, "Authorize", {"idTag": "TAG-QUALQUER"})[2] == {"idTagInfo": {"status": "Accepted"}}
    r = chamar(db, "StartTransaction", {
        "connectorId": 1, "idTag": "TAG-QUALQUER", "meterStart": 0, "timestamp": "2026-08-03T19:10:00Z",
    })
    assert r[2]["idTagInfo"]["status"] == "Accepted"
    parar(db, r[2]["transactionId"], 5000)
    assert db.get(m.SessaoRecarga, r[2]["transactionId"]).status_cobranca == "quarentena"


def test_acao_desconhecida_responde_not_implemented(db):
    r = chamar(db, "TransactionEvent", {})  # ação do OCPP 2.0.1
    assert r[:3] == [4, "1", "NotImplemented"] and r[4] == {}


def test_data_transfer_responde_not_supported(db):
    r = chamar(db, "DataTransfer", {"vendorId": "GoodWe"})
    assert r[:3] == [4, "1", "NotSupported"]


def test_frame_malformado_responde_formation_violation(db):
    r = json.loads(processar_texto(db, CARREGADOR, "isto não é json"))
    assert r[:3] == [4, "", "FormationViolation"]
    aninhado = json.loads(processar_texto(db, CARREGADOR, "[" * 100000 + "]" * 100000))
    assert aninhado[:3] == [4, "", "FormationViolation"]
    assert processar(db, CARREGADOR, [2, "7", "Heartbeat"])[:3] == [4, "7", "FormationViolation"]
    # respostas do carregador a CALLs nossos não têm resposta
    assert processar(db, CARREGADOR, [3, "9", {}]) is None


def test_campo_obrigatorio_ausente(db):
    r = chamar(db, "StartTransaction", {"connectorId": 1, "meterStart": 0, "timestamp": "2026-08-03T19:10:00Z"})
    assert r[:3] == [4, "1", "OccurenceConstraintViolation"]


def test_data_invalida_responde_type_constraint(db):
    r = chamar(db, "StartTransaction", {"connectorId": 1, "idTag": TAG, "meterStart": 0, "timestamp": "ontem"})
    assert r[:3] == [4, "1", "TypeConstraintViolation"]


def test_tipo_errado_responde_type_constraint(db):
    r = chamar(db, "StartTransaction", {
        "connectorId": 1, "idTag": 1204, "meterStart": 0, "timestamp": "2026-08-03T19:10:00Z",
    })
    assert r[:3] == [4, "1", "TypeConstraintViolation"]


def test_data_sem_fuso_e_tratada_como_utc(db):
    transacao = iniciar(db, quando="2026-08-03T19:10:00")
    assert db.get(m.SessaoRecarga, transacao).inicio_em == utc(19, 10)


def test_regra_da_sessao_responde_property_constraint(db):
    parar(db, iniciar(db), 1006200)
    # meterStart abaixo do fim da sessão anterior: o domínio recusa (422)
    r = chamar(db, "StartTransaction", {
        "connectorId": 1, "idTag": TAG, "meterStart": 1000000, "timestamp": "2026-08-04T08:00:00Z",
    })
    assert r[:3] == [4, "1", "PropertyConstraintViolation"]


def test_stop_com_medidor_abaixo_da_ultima_leitura_fecha_e_libera_o_carregador(db):
    transacao = iniciar(db)
    medir(db, transacao, "2026-08-03T20:10:00Z", 1006200)
    assert parar(db, transacao, 1003000) == [3, "1", {}]
    sessao = db.get(m.SessaoRecarga, transacao)
    assert sessao.status_final != "em_andamento"
    assert sessao.leitura_final_kwh == pytest.approx(1006.2)  # último dado válido
    proxima = chamar(db, "StartTransaction", {
        "connectorId": 1, "idTag": TAG, "meterStart": 1006200, "timestamp": "2026-08-04T08:00:00Z",
    })
    assert proxima[2]["idTagInfo"]["status"] == "Accepted"


def test_stop_antes_da_ultima_telemetria_fecha_na_hora_dela(db):
    transacao = iniciar(db)
    medir(db, transacao, "2026-08-03T21:30:00Z", 1006200)
    assert parar(db, transacao, 1006200, quando="2026-08-03T21:00:00Z") == [3, "1", {}]
    sessao = db.get(m.SessaoRecarga, transacao)
    assert sessao.fim_em == utc(21, 30)
    assert sessao.kwh_entregue == pytest.approx(6.2)


def test_status_sem_timestamp_nao_usa_o_relogio_do_servidor(db):
    transacao = iniciar(db)
    chamar(db, "StatusNotification", {"connectorId": 1, "errorCode": "NoError", "status": "SuspendedEV"})
    assert parar(db, transacao, 1006200, quando="2026-08-03T21:00:00Z") == [3, "1", {}]
    sessao = db.get(m.SessaoRecarga, transacao)
    assert sessao.carga_completa_em == utc(19, 10)
    assert sessao.fim_em == utc(21, 0)


def test_stop_de_transacao_inexistente_responde_vazio(db):
    assert parar(db, 0, 1000) == [3, "1", {}]
    assert parar(db, 999, 1000) == [3, "1", {}]


@pytest.mark.parametrize("razao, motivo", [
    ("EVDisconnected", "usuario_desconectou"),
    (None, "usuario_desconectou"),  # sem reason o padrão do OCPP é Local
    ("EmergencyStop", "falha_carregador"),
    ("PowerLoss", "falha_carregador"),
    ("Reboot", "falha_carregador"),
    ("Remote", "limite_operacional"),
    ("Other", "limite_operacional"),
])
def test_motivos_de_parada(db, razao, motivo):
    transacao = iniciar(db)
    parar(db, transacao, 1006200, motivo=razao)
    assert db.get(m.SessaoRecarga, transacao).motivo_encerramento == motivo


def test_status_faulted_registra_evento_erro(db):
    transacao = iniciar(db)
    chamar(db, "StatusNotification", {
        "connectorId": 1, "errorCode": "GroundFailure", "status": "Faulted", "timestamp": "2026-08-03T19:30:00Z",
    })
    tipos = db.scalars(select(m.EventoSessao.tipo_evento).where(m.EventoSessao.sessao_id == transacao)).all()
    assert tipos == ["inicio", "erro"]


def test_status_finishing_nao_marca_carga_completa(db):
    transacao = iniciar(db)
    chamar(db, "StatusNotification", {
        "connectorId": 1, "errorCode": "NoError", "status": "Finishing", "timestamp": "2026-08-03T21:00:00Z",
    })
    assert db.get(m.SessaoRecarga, transacao).carga_completa_em is None


def test_websocket_boot_notification(cliente):
    with cliente.websocket_connect(f"/ocpp/{CARREGADOR}", subprotocols=["ocpp1.6"]) as ws:
        assert ws.accepted_subprotocol == "ocpp1.6"
        boot = {"chargePointVendor": "GoodWe", "chargePointModel": "HCA G2"}
        ws.send_text(json.dumps([2, "42", "BootNotification", boot]))
        r = json.loads(ws.receive_text())
        assert r[:2] == [3, "42"] and r[2]["status"] == "Accepted"


def test_websocket_sem_subprotocolo_e_recusado(cliente):
    # a spec manda completar o handshake sem subprotocolo e fechar logo em seguida
    with cliente.websocket_connect(f"/ocpp/{CARREGADOR}") as ws:
        assert ws.accepted_subprotocol is None
        with pytest.raises(WebSocketDisconnect) as fechamento:
            ws.receive_text()
    assert fechamento.value.code == 1002

"""Testes da normalização do SEMS com um retorno de exemplo (não é dado real)."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import models as m
from app.database import get_db
from app.integracoes.sems import (
    ClienteSEMS,
    LeituraCarregador,
    localizar_ev_charger,
    normalizar_leitura,
    sincronizar_sems,
)
from app.main import app
from app.seed import CARREGADOR_ID
from app.sessoes import iniciar_sessao

RETORNO_EXEMPLO = {
    "data": {
        "info": {"stationname": "LAB FIAP Eco Smart Home"},
        "inverter": [
            {"name": "Inversor híbrido", "type": "GW5K-ET", "d": {"pac": 1200, "etotal": 8000}},
            {"name": "EV Charger", "type": "HCA G2", "d": {"power": 6900, "total_energy": 1532.4, "status": "charging"}},
        ],
    }
}


def test_encontra_o_carregador_entre_os_equipamentos():
    equip = localizar_ev_charger(RETORNO_EXEMPLO)
    assert equip["type"] == "HCA G2"


def test_normaliza_watts_para_kw_e_traduz_status():
    leitura = normalizar_leitura(localizar_ev_charger(RETORNO_EXEMPLO))
    assert leitura.potencia_kw == 6.9
    assert leitura.energia_acumulada_kwh == 1532.4
    assert leitura.status == "carregando"


class ClienteFalso:
    """Faz o papel do ClienteSEMS sem rede."""

    def leitura_carregador(self, planta_id):
        return LeituraCarregador(datetime(2026, 8, 3, 20, 10), 6.9, 1006.9, "carregando")


@pytest.fixture
def cliente(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_sincronizar_grava_telemetria_na_sessao_aberta(db):
    sessao = iniciar_sessao(db, CARREGADOR_ID, "RFID-A1204-01", datetime(2026, 8, 3, 19, 10), 1000.0)
    r = sincronizar_sems(db, ClienteFalso(), "planta", CARREGADOR_ID)
    assert r["sessao_id"] == sessao.id
    assert db.get(m.SessaoRecarga, sessao.id).leitura_final_kwh == pytest.approx(1006.9)
    assert db.get(m.SessaoRecarga, sessao.id).potencia_max_kw == pytest.approx(6.9)


def test_sincronizar_sem_sessao_nao_grava(db):
    r = sincronizar_sems(db, ClienteFalso(), "planta", CARREGADOR_ID)
    assert r == {"sessao_id": None, "potencia_kw": 6.9, "energia_acumulada_kwh": 1006.9, "status": "carregando"}
    assert db.query(m.EventoSessao).count() == 0


def test_rota_sems_sem_configuracao_responde_503(cliente, monkeypatch):
    for variavel in ("EVCO_SEMS_USUARIO", "EVCO_SEMS_SENHA", "EVCO_SEMS_PLANTA_ID"):
        monkeypatch.delenv(variavel, raising=False)
    r = cliente.post("/integracoes/sems/sincronizar")
    assert r.status_code == 503


def test_rota_sems_com_cliente_falso(cliente, monkeypatch):
    monkeypatch.setattr(ClienteSEMS, "do_ambiente", lambda: ClienteFalso())
    monkeypatch.setenv("EVCO_SEMS_PLANTA_ID", "planta-lab")
    cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T19:10:00", "leitura_inicial_kwh": 1000,
    })
    r = cliente.post("/integracoes/sems/sincronizar")
    assert r.status_code == 200
    assert r.json()["sessao_id"] is not None
    assert r.json()["energia_acumulada_kwh"] == pytest.approx(1006.9)

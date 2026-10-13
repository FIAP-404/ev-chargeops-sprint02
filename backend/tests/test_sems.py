"""Testes da normalização do SEMS com um retorno de exemplo (não é dado real)."""
from app.integracoes.sems import localizar_ev_charger, normalizar_leitura

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

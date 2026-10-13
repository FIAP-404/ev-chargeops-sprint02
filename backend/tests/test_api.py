"""Teste de ponta a ponta pela API HTTP (o caminho que o carregador/gateway usaria)."""
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


@pytest.fixture
def cliente(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_fluxo_completo_pela_api(cliente):
    r = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T19:10:00", "leitura_inicial_kwh": 1000,
    })
    assert r.status_code == 201
    sessao_id = r.json()["id"]
    assert r.json()["unidade"] == "A-1204"

    # segundo usuário encontra o carregador ocupado
    r2 = cliente.post("/sessoes/iniciar", json={"codigo_rfid": "RFID-A0302-01", "leitura_inicial_kwh": 1000})
    assert r2.status_code == 409

    for i, leitura in enumerate([1006.2, 1012.4, 1018.6], start=1):
        r = cliente.post(f"/sessoes/{sessao_id}/telemetria", json={
            "timestamp": f"2026-08-03T{19 + i}:10:00", "potencia_kw": 6.2, "energia_acumulada_kwh": leitura,
        })
        assert r.status_code == 200

    r = cliente.post(f"/sessoes/{sessao_id}/encerrar", json={
        "motivo": "usuario_desconectou", "fim_em": "2026-08-03T22:40:00", "leitura_final_kwh": 1018.6,
    })
    corpo = r.json()
    assert corpo["kwh_entregue"] == pytest.approx(18.6)
    assert corpo["status_cobranca"] == "liberada"

    faturas = cliente.post("/faturas/gerar", params={"mes": "2026-08"}).json()
    f1204 = next(f for f in faturas if f["unidade"] == "A-1204")
    # 18,6 kWh × 0,95 = 17,67 ; perdas 0,88 ; infra 20,00 -> 38,55
    assert f1204["valor_total"] == pytest.approx(38.55)
    assert [linha["parcela"] for linha in f1204["memoria_calculo"]["linhas"]][0] == "Energia"

    detalhe = cliente.get(f"/sessoes/{sessao_id}").json()
    assert [e["tipo"] for e in detalhe["eventos"]] == ["inicio", "telemetria", "telemetria", "telemetria", "fim"]


def test_validacao_de_entrada(cliente):
    r = cliente.post("/sessoes/iniciar", json={"codigo_rfid": "X", "leitura_inicial_kwh": -5})
    assert r.status_code == 422


def test_painel_do_usuario_e_dashboard(cliente):
    usuarios = cliente.get("/usuarios").json()
    marina = next(u for u in usuarios if u["unidade"] == "A-1204")
    assert len(marina["veiculos"]) == 2
    painel = cliente.get(f"/usuarios/{marina['id']}/painel", params={"mes": "2026-08"}).json()
    assert painel["unidade"]["rotulo"] == "A-1204"
    resumo = cliente.get("/dashboard/resumo", params={"mes": "2026-08"}).json()
    assert "kpis" in resumo and len(resumo["consumo_por_unidade"]) == 12

"""Teste de ponta a ponta pela API HTTP (o caminho que o carregador/gateway usaria)."""
import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import models as m
from app.database import get_db
from app.ia import servico as ia
from app.main import app

from .test_ia_modelo import historico


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


def test_iniciar_sessao_pelo_app(cliente, db):
    rafael = db.scalar(select(m.Usuario).where(m.Usuario.email == "rafael.a302@exemplo.com"))
    corpo = {"qr_token": "QR-HCA01-7F3A", "usuario_id": rafael.id, "inicio_em": "2026-08-03T19:10:00",
             "leitura_inicial_kwh": 1000}
    r = cliente.post("/sessoes/iniciar-app", json={**corpo, "qr_token": "QR-ERRADO"})
    assert r.status_code == 403
    r = cliente.post("/sessoes/iniciar-app", json=corpo)
    assert r.status_code == 201
    assert r.json()["metodo_identificacao"] == "app_qr" and r.json()["unidade"] == "A-302"


def test_painel_do_usuario_e_dashboard(cliente):
    usuarios = cliente.get("/usuarios").json()
    marina = next(u for u in usuarios if u["unidade"] == "A-1204")
    assert len(marina["veiculos"]) == 2
    painel = cliente.get(f"/usuarios/{marina['id']}/painel", params={"mes": "2026-08"}).json()
    assert painel["unidade"]["rotulo"] == "A-1204"
    resumo = cliente.get("/dashboard/resumo", params={"mes": "2026-08"}).json()
    assert "kpis" in resumo and len(resumo["consumo_por_unidade"]) == 12
    assert len(resumo["consumo_por_carregador"]) == 1
    assert resumo["consumo_por_carregador"][0]["carregador_id"] == "goodwe_hca_g2_l1"


def _telemetria_e_encerrar(cliente, sessao_id, leituras, fim_em):
    for i, leitura in enumerate(leituras, start=1):
        r = cliente.post(f"/sessoes/{sessao_id}/telemetria", json={
            "timestamp": f"2026-08-03T{19 + i}:10:00", "potencia_kw": 6.2, "energia_acumulada_kwh": leitura,
        })
        assert r.status_code == 200
    r = cliente.post(f"/sessoes/{sessao_id}/encerrar", json={
        "motivo": "usuario_desconectou", "fim_em": fim_em, "leitura_final_kwh": leituras[-1],
    })
    assert r.status_code == 200


def test_relatorio_por_carregador(cliente, db):
    # o cenário simulado tem um carregador; aqui entra um segundo para separar o consumo
    db.add(m.Carregador(
        id="teste_l2", fabricante="GoodWe", modelo="HCA G2 7kW", numero_serie="HCA-G2-TESTE-0002",
        localizacao="Estacionamento L2", potencia_max_kw=7.4,
    ))
    db.commit()

    a = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T19:10:00", "leitura_inicial_kwh": 1000,
    })
    # com o L1 ocupado, o L2 aceita outra sessão ao mesmo tempo
    b = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A0302-01", "carregador_id": "teste_l2", "inicio_em": "2026-08-03T19:30:00",
        "leitura_inicial_kwh": 1000,
    })
    assert a.status_code == 201 and b.status_code == 201
    ocupado = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A0507-01", "carregador_id": "teste_l2", "inicio_em": "2026-08-03T20:00:00",
        "leitura_inicial_kwh": 1000,
    })
    assert ocupado.status_code == 409

    _telemetria_e_encerrar(cliente, a.json()["id"], [1006.2, 1012.4, 1018.6], "2026-08-03T22:40:00")
    _telemetria_e_encerrar(cliente, b.json()["id"], [1006.2, 1012.4], "2026-08-03T21:40:00")

    resumo = cliente.get("/dashboard/resumo", params={"mes": "2026-08"}).json()
    por = {c["carregador_id"]: c for c in resumo["consumo_por_carregador"]}
    assert len(por) == 2
    assert por["goodwe_hca_g2_l1"]["sessoes"] == 1
    assert por["goodwe_hca_g2_l1"]["kwh"] == pytest.approx(18.6)
    assert por["teste_l2"]["sessoes"] == 1
    assert por["teste_l2"]["kwh"] == pytest.approx(12.4)
    assert por["teste_l2"]["localizacao"] == "Estacionamento L2"
    assert [por["goodwe_hca_g2_l1"]["tentativas_ocupado"], por["teste_l2"]["tentativas_ocupado"]] == [0, 1]
    assert sum(c["kwh"] for c in por.values()) == pytest.approx(resumo["kpis"]["kwh"])
    assert all(c["valor_sessoes"] > 0 for c in por.values())


CABECALHO_CSV = [
    "unidade", "mes", "qtd_sessoes", "kwh_total", "energia", "perdas", "infra",
    "ociosidade", "ajustes", "total", "status", "sessoes_pendentes",
]


def test_exportar_csv_do_mes(cliente):
    r = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T19:10:00", "leitura_inicial_kwh": 1000,
    })
    _telemetria_e_encerrar(cliente, r.json()["id"], [1006.2, 1012.4, 1018.6], "2026-08-03T22:40:00")
    cliente.post("/faturas/gerar", params={"mes": "2026-08"})

    r = cliente.get("/faturas/exportar.csv", params={"mes": "2026-08"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")  # BOM: o Excel abre com acentos
    linhas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert linhas[0] == CABECALHO_CSV
    assert len(linhas) - 1 == len(cliente.get("/unidades").json())

    # o total de cada unidade é o mesmo da tela, com vírgula decimal
    faturas = {f["unidade"]: f for f in cliente.get("/faturas", params={"mes": "2026-08"}).json()}
    for linha in linhas[1:]:
        assert float(linha[9].replace(",", ".")) == pytest.approx(faturas[linha[0]]["valor_total"])
    a1204 = next(linha for linha in linhas if linha[0] == "A-1204")
    assert a1204[3] == "18,600" and a1204[9] == "38,55"


def test_exportar_csv_mes_invalido_ou_vazio(cliente):
    # 400 (e não 422) também prova que a rota vem antes de /faturas/{fatura_id}
    assert cliente.get("/faturas/exportar.csv", params={"mes": "2026-13"}).status_code == 400
    assert cliente.get("/faturas/exportar.csv", params={"mes": "agosto"}).status_code == 400
    assert cliente.get("/faturas/exportar.csv", params={"mes": "2025-01"}).status_code == 404


def test_avisos_do_morador_listar_e_marcar_como_lida(cliente):
    r = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T19:10:00", "leitura_inicial_kwh": 1000,
    })
    sessao = r.json()
    _telemetria_e_encerrar(cliente, sessao["id"], [1006.2, 1012.4, 1018.6], "2026-08-03T22:40:00")
    painel = f"/usuarios/{sessao['usuario_id']}/painel"
    assert cliente.get(painel, params={"mes": "2026-08"}).json()["notificacoes_nao_lidas"] == 1

    avisos = cliente.get(f"/usuarios/{sessao['usuario_id']}/notificacoes").json()
    assert len(avisos) == 1 and avisos[0]["sessao_id"] == sessao["id"]
    assert avisos[0]["titulo"] == "Recarga encerrada: 18,6 kWh" and avisos[0]["lida"] is False

    r = cliente.post(f"/notificacoes/{avisos[0]['id']}/lida")
    assert r.status_code == 200 and r.json()["lida"] is True and r.json()["lida_em"]
    assert cliente.get(painel, params={"mes": "2026-08"}).json()["notificacoes_nao_lidas"] == 0
    nao_lidas = cliente.get(f"/usuarios/{sessao['usuario_id']}/notificacoes", params={"nao_lidas": True})
    assert nao_lidas.json() == []
    assert cliente.post("/notificacoes/999999/lida").status_code == 404
    assert cliente.get("/usuarios/999999/notificacoes").status_code == 404
    assert cliente.get(f"/usuarios/{sessao['usuario_id']}/notificacoes", params={"limite": 0}).status_code == 422


def test_status_da_ia_em_cold_start(cliente):
    anomalias = cliente.get("/ia/status").json()["anomalias"]
    assert anomalias["cold_start"] is True and anomalias["treinado"] is False
    assert anomalias["minimo_sessoes"] == 40 and anomalias["sessoes_elegiveis"] == 0


def test_gerar_faturas_retreina_a_ia(cliente, db):
    historico(db)
    r = cliente.post("/faturas/gerar", params={"mes": "2026-08"})
    assert r.status_code == 200
    assert ia.detector().treinado
    assert cliente.get("/ia/status").json()["anomalias"]["cold_start"] is False

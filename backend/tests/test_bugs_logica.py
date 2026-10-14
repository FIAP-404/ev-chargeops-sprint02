"""Casos de borda do ciclo da sessão e do fechamento do mês."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import models as m
from app.database import get_db
from app.faturamento import calcular_unidade, gerar_faturas, valor_da_sessao
from app.main import app
from app.seed import CARREGADOR_ID
from app.sessoes import ErroSessao, encerrar_sessao, iniciar_sessao, registrar_telemetria, revisar_sessao

from .test_fluxo_sessao import T0, avisos, recarga, usuario


@pytest.fixture
def cliente(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _fatura(db, mes, unidade="A-1204"):
    return {f.unidade.rotulo: f for f in gerar_faturas(db, mes)}[unidade]


def test_sessao_aberta_no_fechamento_fica_pendente(db):
    inicio = datetime(2026, 8, 31, 23, 0)
    s = iniciar_sessao(db, CARREGADOR_ID, "RFID-A1204-01", inicio, 1000)
    registrar_telemetria(db, s.id, inicio + timedelta(hours=1), 6.2, 1006.2)

    f = _fatura(db, "2026-08")
    assert f.status == "fechada_com_pendencias"
    assert f.sessoes_pendentes == 1 and f.kwh_total == 0

    # a sessão termina em setembro, mas pertence ao mês em que começou
    encerrar_sessao(db, s.id, "usuario_desconectou", inicio + timedelta(hours=2), 1012.4)
    assert f.status == "desatualizada"

    f = _fatura(db, "2026-08")
    assert f.status == "fechada" and f.sessoes_pendentes == 0
    assert f.kwh_total == pytest.approx(12.4)


def test_revisao_apos_fechamento_marca_fatura_desatualizada(db):
    suspeita = recarga(db, "RFID-A1204-02", 40, horas_carga=6)
    assert suspeita.status_cobranca == m.COBRANCA_REVISAO_IA
    f = _fatura(db, "2026-08")
    assert f.status == "fechada_com_pendencias"

    revisar_sessao(db, suspeita.id, "liberar", observacao="leitura conferida")
    assert f.status == "desatualizada"
    assert _fatura(db, "2026-08").kwh_total == pytest.approx(40)


def test_regerar_mes_mantem_parametros_da_fatura(db):
    recarga(db, "RFID-A1204-01", 18.6)
    f = _fatura(db, "2026-08")
    assert f.valor_total == pytest.approx(38.55)

    # a nova tarifa vale para os meses ainda abertos, não para o que já foi cobrado
    db.get(m.ParametrosRateio, 1).tarifa_kwh = 1.50
    db.commit()
    assert _fatura(db, "2026-08").valor_total == pytest.approx(38.55)
    assert calcular_unidade(db, f.unidade_id, "2026-08")["valor_total"] == pytest.approx(38.55)


def test_sessao_atribuida_depois_do_fechamento_usa_parametros_do_mes(cliente, db):
    s = recarga(db, "CARTAO-NAO-CADASTRADO", 5)
    gerar_faturas(db, "2026-08")
    db.get(m.ParametrosRateio, 1).tarifa_kwh = 2.00
    db.commit()

    # 5 kWh × R$ 0,95 (tarifa da fatura de agosto) + 5% de perdas = R$ 4,99, e não R$ 10,50
    assert valor_da_sessao(db, s) == pytest.approx(4.99)
    [retida] = cliente.get("/dashboard/resumo", params={"mes": "2026-08"}).json()["sessoes_retidas"]
    assert retida["valor_sessao"] == pytest.approx(4.99)
    rafael = usuario(db, "rafael.a302@exemplo.com")
    with pytest.raises(ErroSessao, match="valor da sessão"):
        revisar_sessao(db, s.id, "liberar", ajuste_valor=5.00, usuario_id=rafael.id)

    revisar_sessao(db, s.id, "liberar", usuario_id=rafael.id, observacao="visitante do A-302")
    [aviso] = avisos(db, s.id)
    assert "R$ 4,99" in aviso.mensagem
    f = _fatura(db, "2026-08", "A-302")
    assert f.valor_total - f.valor_infra == pytest.approx(4.99)


def test_encerrar_com_fuso_z_e_menos_tres(cliente):
    # 22:10 UTC = 19:10 em Brasília; o resultado não depende do fuso da máquina
    r = cliente.post("/sessoes/iniciar", json={
        "codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T22:10:00Z", "leitura_inicial_kwh": 1000,
    })
    assert r.status_code == 201
    r = cliente.post(f"/sessoes/{r.json()['id']}/encerrar", json={
        "motivo": "usuario_desconectou", "fim_em": "2026-08-03T21:40:00-03:00", "leitura_final_kwh": 1015.5,
    })
    assert r.status_code == 200
    assert r.json()["duracao_min"] == 150


def _status_do_erro(funcao, *args):
    with pytest.raises(ErroSessao) as erro:
        funcao(*args)
    return erro.value.status_http


def test_telemetria_anterior_ao_inicio_422(db):
    s = iniciar_sessao(db, CARREGADOR_ID, "RFID-A1204-01", T0, 1000)
    assert _status_do_erro(registrar_telemetria, db, s.id, T0 - timedelta(minutes=5), 6.2, 1001) == 422
    assert s.carga_completa_em is None


def test_fim_antes_da_ultima_telemetria_422(db):
    s = iniciar_sessao(db, CARREGADOR_ID, "RFID-A1204-01", T0, 1000)
    registrar_telemetria(db, s.id, T0 + timedelta(hours=2), 0.0, 1012.4, "carga_completa")
    fim = T0 + timedelta(hours=1)
    assert _status_do_erro(encerrar_sessao, db, s.id, "usuario_desconectou", fim, 1012.4) == 422
    assert s.status_final == m.STATUS_EM_ANDAMENTO


def test_inicio_sobreposto_a_sessao_encerrada_422(db):
    recarga(db, "RFID-A1204-01", 18.6)  # 19:10 às 22:10
    inicio = T0 + timedelta(hours=2)
    assert _status_do_erro(iniciar_sessao, db, CARREGADOR_ID, "RFID-A0302-01", inicio, 1018.6) == 422


def test_leitura_inicial_menor_que_anterior_422(db):
    recarga(db, "RFID-A1204-01", 18.6)  # medidor termina em 1018,6
    inicio = T0 + timedelta(hours=4)
    assert _status_do_erro(iniciar_sessao, db, CARREGADOR_ID, "RFID-A0302-01", inicio, 1000) == 422


def test_leitura_inicial_omitida_usa_ultima_leitura(cliente, db):
    primeira = iniciar_sessao(db, CARREGADOR_ID, "RFID-A0302-01", T0 - timedelta(days=1))
    assert primeira.leitura_inicial_kwh == 0  # carregador sem histórico
    encerrar_sessao(db, primeira.id, "usuario_desconectou", T0 - timedelta(hours=20), 18.6)

    r = cliente.post("/sessoes/iniciar", json={"codigo_rfid": "RFID-A1204-01", "inicio_em": "2026-08-03T19:10:00"})
    assert r.status_code == 201
    assert r.json()["leitura_inicial_kwh"] == pytest.approx(18.6)


def test_leitura_final_menor_que_telemetria_422(db):
    s = iniciar_sessao(db, CARREGADOR_ID, "RFID-A1204-01", T0, 1000)
    registrar_telemetria(db, s.id, T0 + timedelta(hours=1), 6.2, 1006.2)
    fim = T0 + timedelta(hours=2)
    assert _status_do_erro(encerrar_sessao, db, s.id, "usuario_desconectou", fim, 1003.0) == 422


def test_listar_sessoes_mes_invalido_400(cliente):
    assert cliente.get("/sessoes", params={"mes": "xx"}).status_code == 400
    assert cliente.get("/dashboard/resumo", params={"mes": "xx"}).status_code == 400
    usuario = next(u for u in cliente.get("/usuarios").json() if u["unidade"])
    assert cliente.get(f"/usuarios/{usuario['id']}/painel", params={"mes": "2026-13"}).status_code == 400


def test_listar_faturas_mes_invalido_400(cliente):
    assert cliente.get("/faturas", params={"mes": "xx"}).status_code == 400
    assert cliente.get("/faturas", params={"mes": "2026-08"}).status_code == 200


def test_gerar_faturas_mes_invalido_400(cliente, db):
    # "2026-1" passaria em limites_do_mes e gravaria faturas que GET /faturas e o CSV recusam
    assert cliente.post("/faturas/gerar", params={"mes": "2026-1"}).status_code == 400
    assert db.scalar(select(m.Fatura)) is None


def test_csv_ordena_unidades_pelo_numero(cliente):
    cliente.post("/faturas/gerar", params={"mes": "2026-08"})
    r = cliente.get("/faturas/exportar.csv", params={"mes": "2026-08"})
    unidades = [linha.split(";")[0] for linha in r.content.decode("utf-8-sig").splitlines()[1:]]
    chave = [(u.split("-")[0], int(u.split("-")[1])) for u in unidades]
    assert chave == sorted(chave)
    assert unidades.index("A-302") < unidades.index("A-1204")

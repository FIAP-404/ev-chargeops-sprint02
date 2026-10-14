"""Casos de borda do ciclo da sessão e do fechamento do mês."""
from datetime import datetime, timedelta

import pytest

from app import models as m
from app.faturamento import calcular_unidade, gerar_faturas
from app.seed import CARREGADOR_ID
from app.sessoes import encerrar_sessao, iniciar_sessao, registrar_telemetria, revisar_sessao

from .test_fluxo_sessao import recarga


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

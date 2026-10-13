"""Testes do motor de rateio (sem banco, só a regra de cálculo)."""
from decimal import Decimal

from app.rateio import ParametrosCalculo, SessaoRateio, calcular_rateio

PARAMS_SPRINT01 = ParametrosCalculo(
    tarifa_kwh=0.95,
    perdas_percentual=5,
    taxa_infra=20.0,
    ociosidade_ativa=False,
)


def test_exemplo_da_sprint01_unidade_1204():
    """Exemplo simulado da seção 5.3: 42 kWh, R$ 0,95, 5% de perdas, R$ 20 de taxa = R$ 61,90."""
    sessoes = [SessaoRateio(sessao_id=1, kwh=18.6), SessaoRateio(sessao_id=2, kwh=23.4)]
    r = calcular_rateio(sessoes, PARAMS_SPRINT01)

    assert r.kwh_total == Decimal("42.0")
    assert r.valor_energia == Decimal("39.90")
    assert r.valor_perdas == Decimal("2.00")  # 1,995 arredonda para 2,00
    assert r.valor_infra == Decimal("20.00")
    assert r.valor_ociosidade == Decimal("0.00")
    assert r.valor_total == Decimal("61.90")


def test_unidade_sem_uso_nao_paga_taxa_por_padrao():
    r = calcular_rateio([], PARAMS_SPRINT01)
    assert r.valor_total == Decimal("0.00")
    assert r.valor_infra == Decimal("0.00")


def test_unidade_sem_uso_paga_taxa_se_regra_aprovar():
    p = ParametrosCalculo(tarifa_kwh=0.95, perdas_percentual=5, taxa_infra=20, cobrar_infra_sem_uso=True)
    r = calcular_rateio([], p)
    assert r.valor_total == Decimal("20.00")


def test_ociosidade_cobra_so_o_que_passa_da_tolerancia():
    p = ParametrosCalculo(
        tarifa_kwh=1.0,
        perdas_percentual=0,
        taxa_infra=0,
        ociosidade_ativa=True,
        tolerancia_ociosidade_min=30,
        penalidade_ociosidade_hora=6.0,
    )
    # 90 min ociosos -> 60 min cobrados -> R$ 6,00 ; 20 min ociosos -> dentro da tolerância
    sessoes = [
        SessaoRateio(sessao_id=1, kwh=10, minutos_ociosos=90),
        SessaoRateio(sessao_id=2, kwh=5, minutos_ociosos=20),
    ]
    r = calcular_rateio(sessoes, p)
    assert r.minutos_ociosos_cobrados == Decimal("60")
    assert r.valor_ociosidade == Decimal("6.00")
    assert r.valor_total == Decimal("21.00")


def test_ociosidade_desativada_nao_cobra():
    r = calcular_rateio([SessaoRateio(sessao_id=1, kwh=10, minutos_ociosos=600)], PARAMS_SPRINT01)
    assert r.valor_ociosidade == Decimal("0.00")


def test_credito_por_falha_desconta_mas_nao_fica_negativo():
    sessoes = [SessaoRateio(sessao_id=1, kwh=2, ajuste_valor=500.0)]
    r = calcular_rateio(sessoes, PARAMS_SPRINT01)
    assert r.ajustes == Decimal("500.00")
    assert r.valor_total == Decimal("0.00")


def test_dois_veiculos_da_mesma_unidade_sao_consolidados():
    """Sprint 01: sessões separadas por veículo, mas consolidadas na fatura da unidade."""
    sessoes = [
        SessaoRateio(sessao_id=1, kwh=10, veiculo="ABC1D23"),
        SessaoRateio(sessao_id=2, kwh=5, veiculo="XYZ9K87"),
        SessaoRateio(sessao_id=3, kwh=2.5, veiculo="ABC1D23"),
    ]
    r = calcular_rateio(sessoes, PARAMS_SPRINT01)
    assert r.kwh_total == Decimal("17.5")
    assert r.por_veiculo == {"ABC1D23": 12.5, "XYZ9K87": 5.0}


def test_memoria_de_calculo_explica_cada_parcela():
    r = calcular_rateio([SessaoRateio(sessao_id=1, kwh=42)], PARAMS_SPRINT01)
    parcelas = {linha["parcela"]: linha for linha in r.memoria}
    assert parcelas["Energia"]["regra"] == "42 kWh × R$ 0,95/kWh"
    assert parcelas["Perdas técnicas"]["regra"] == "5% sobre a energia"
    # a soma das linhas da memória bate com o total
    assert round(sum(linha["valor"] for linha in r.memoria), 2) == float(r.valor_total)

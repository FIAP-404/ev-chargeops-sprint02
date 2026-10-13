"""Motor de rateio do EV ChargeOps.

Implementa a fórmula definida na seção 5.3 da Sprint 01:

    Valor individual do mês =
        (kWh consumidos pela unidade × tarifa efetiva por kWh)
      + parcela proporcional de perdas técnicas
      + taxa fixa de infraestrutura/manutenção
      + eventuais penalidades de ociosidade
      - ajustes/créditos por falha ou interrupção

Este módulo é "puro": não acessa banco nem API. Recebe as sessões já filtradas
e os parâmetros, e devolve o valor de cada parcela junto com a memória de
cálculo (o texto que explica de onde veio cada valor). Isso facilita testar o
cálculo isoladamente (tests/test_rateio.py).

Usamos Decimal em vez de float para dinheiro, porque float gera erros de
arredondamento (0.1 + 0.2 != 0.3). Cada parcela é arredondada para 2 casas
com ROUND_HALF_UP, que é o arredondamento "de escola" (1,995 -> 2,00).
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class ParametrosCalculo:
    tarifa_kwh: float
    perdas_percentual: float
    taxa_infra: float
    cobrar_infra_sem_uso: bool = False
    ociosidade_ativa: bool = False
    tolerancia_ociosidade_min: float = 30.0
    penalidade_ociosidade_hora: float = 0.0


@dataclass
class SessaoRateio:
    """Dados mínimos de uma sessão para o cálculo."""

    sessao_id: int
    kwh: float
    minutos_ociosos: float = 0.0
    ajuste_valor: float = 0.0  # crédito concedido pelo gestor (valor positivo = desconto)
    veiculo: str = ""


@dataclass
class ResultadoRateio:
    qtd_sessoes: int
    kwh_total: Decimal
    valor_energia: Decimal
    valor_perdas: Decimal
    valor_infra: Decimal
    valor_ociosidade: Decimal
    ajustes: Decimal
    valor_total: Decimal
    minutos_ociosos_cobrados: Decimal
    memoria: list[dict] = field(default_factory=list)
    por_veiculo: dict[str, float] = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {
            "qtd_sessoes": self.qtd_sessoes,
            "kwh_total": float(self.kwh_total),
            "valor_energia": float(self.valor_energia),
            "valor_perdas": float(self.valor_perdas),
            "valor_infra": float(self.valor_infra),
            "valor_ociosidade": float(self.valor_ociosidade),
            "ajustes": float(self.ajustes),
            "valor_total": float(self.valor_total),
            "minutos_ociosos_cobrados": float(self.minutos_ociosos_cobrados),
            "memoria": self.memoria,
            "kwh_por_veiculo": self.por_veiculo,
        }


def _d(valor) -> Decimal:
    # converte via str para não herdar a imprecisão do float
    return Decimal(str(valor))


def _reais(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt(valor: Decimal) -> str:
    """Formata no padrão brasileiro: 1234.5 -> 'R$ 1.234,50'."""
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _num(valor: Decimal) -> str:
    """Número sem zeros sobrando e com vírgula decimal: 42.50 -> '42,5'."""
    texto = f"{valor.normalize():f}" if valor != 0 else "0"
    return texto.replace(".", ",")


def minutos_ociosos_cobraveis(minutos_ociosos: float, p: ParametrosCalculo) -> Decimal:
    """Ociosidade só é cobrada no que passar da tolerância, e só se a regra estiver ativa."""
    if not p.ociosidade_ativa:
        return Decimal("0")
    excedente = _d(minutos_ociosos) - _d(p.tolerancia_ociosidade_min)
    return max(excedente, Decimal("0"))


def calcular_rateio(sessoes: list[SessaoRateio], p: ParametrosCalculo) -> ResultadoRateio:
    """Calcula o valor de uma unidade no período a partir das sessões liberadas para cobrança."""
    kwh_total = sum((_d(s.kwh) for s in sessoes), Decimal("0"))
    tarifa = _d(p.tarifa_kwh)

    # 1) energia: kWh × tarifa
    valor_energia = _reais(kwh_total * tarifa)

    # 2) perdas técnicas: percentual sobre o valor da energia
    valor_perdas = _reais(valor_energia * _d(p.perdas_percentual) / Decimal("100"))

    # 3) taxa fixa de infraestrutura: só cobra quem usou, a não ser que a regra
    #    interna aprove cobrar disponibilidade de todos
    usou = kwh_total > 0
    if usou or p.cobrar_infra_sem_uso:
        valor_infra = _reais(_d(p.taxa_infra))
    else:
        valor_infra = Decimal("0.00")

    # 4) ociosidade: minutos acima da tolerância × valor por hora
    minutos_cobrados = sum((minutos_ociosos_cobraveis(s.minutos_ociosos, p) for s in sessoes), Decimal("0"))
    valor_ociosidade = _reais(minutos_cobrados / Decimal("60") * _d(p.penalidade_ociosidade_hora))

    # 5) ajustes/créditos aprovados pelo gestor
    ajustes = _reais(sum((_d(s.ajuste_valor) for s in sessoes), Decimal("0")))

    bruto = valor_energia + valor_perdas + valor_infra + valor_ociosidade - ajustes
    valor_total = max(_reais(bruto), Decimal("0.00"))  # crédito nunca gera valor negativo

    memoria = [
        {
            "parcela": "Energia",
            "regra": f"{_num(kwh_total)} kWh × {_fmt(tarifa)}/kWh",
            "valor": float(valor_energia),
        },
        {
            "parcela": "Perdas técnicas",
            "regra": f"{_num(_d(p.perdas_percentual))}% sobre a energia",
            "valor": float(valor_perdas),
        },
        {
            "parcela": "Infraestrutura",
            "regra": (
                "Taxa fixa aprovada"
                if valor_infra > 0
                else "Sem uso no mês: taxa não cobrada (regra de disponibilidade desativada)"
            ),
            "valor": float(valor_infra),
        },
        {
            "parcela": "Ociosidade",
            "regra": (
                f"{_num(minutos_cobrados)} min acima da tolerância de "
                f"{_num(_d(p.tolerancia_ociosidade_min))} min × {_fmt(_d(p.penalidade_ociosidade_hora))}/h"
                if p.ociosidade_ativa and minutos_cobrados > 0
                else ("Sem ocorrências no mês" if p.ociosidade_ativa else "Regra de ociosidade desativada")
            ),
            "valor": float(valor_ociosidade),
        },
        {
            "parcela": "Ajustes e créditos",
            "regra": "Créditos por falha/interrupção aprovados pelo gestor" if ajustes > 0 else "Nenhum ajuste",
            "valor": float(-ajustes),
        },
    ]

    por_veiculo: dict[str, float] = {}
    for s in sessoes:
        chave = s.veiculo or "sem veículo"
        por_veiculo[chave] = round(por_veiculo.get(chave, 0.0) + s.kwh, 3)

    return ResultadoRateio(
        qtd_sessoes=len(sessoes),
        kwh_total=kwh_total,
        valor_energia=valor_energia,
        valor_perdas=valor_perdas,
        valor_infra=valor_infra,
        valor_ociosidade=valor_ociosidade,
        ajustes=ajustes,
        valor_total=valor_total,
        minutos_ociosos_cobrados=minutos_cobrados,
        memoria=memoria,
        por_veiculo=por_veiculo,
    )

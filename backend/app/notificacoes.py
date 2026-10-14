"""Avisos ao morador (passo 9 do fluxo 5.2: "usuário recebe resumo da sessão").

Cada aviso é gravado na tabela notificacoes, que alimenta a caixa de avisos da
Área do morador, e também passa pelo enviador configurado. No protótipo o
enviador só escreve uma linha de log.

As funções daqui não fazem commit: quem chama (encerrar_sessao, revisar_sessao)
grava o aviso na mesma transação da sessão. Por isso o envio acontece antes do
commit; para o log isso não importa, mas um canal real (WhatsApp, e-mail ou
push) deve enviar só depois do commit (por exemplo, lendo os avisos gravados),
para nunca avisar o morador de algo que foi desfeito.
"""
import logging
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app import models as m
from app.faturamento import parametros_do_mes, valor_da_sessao
from app.ia.anomalias import br
from app.rateio import SessaoRateio, calcular_rateio

log = logging.getLogger("evchargeops.avisos")

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


class Enviador(Protocol):
    """Canal de envio. É aqui que entram WhatsApp, e-mail ou push: uma classe
    com este método, registrada com usar_enviador() (ver o aviso sobre o commit
    no topo do módulo)."""

    def enviar(self, n: m.Notificacao, usuario: m.Usuario) -> None: ...


class EnviadorConsole:
    """Padrão do protótipo: uma linha de log por aviso (sem print, para não
    misturar com a saída do simulador)."""

    def enviar(self, n: m.Notificacao, usuario: m.Usuario) -> None:
        log.info("Aviso para %s <%s>: %s. %s", usuario.nome, usuario.email, n.titulo, n.mensagem)


enviador: Enviador = EnviadorConsole()


def usar_enviador(novo: Enviador) -> None:
    global enviador
    enviador = novo


def _reais(valor: float) -> str:
    return f"R$ {br(valor, 2)}"


def _duracao(minutos: float) -> str:
    """135 -> '2 h 15 min' (mesmo formato da tela)."""
    h, mins = divmod(round(minutos), 60)
    return f"{h} h {mins:02d} min" if h else f"{mins} min"


def _quando(sessao: m.SessaoRecarga) -> str:
    return sessao.inicio_em.strftime("%d/%m %H:%M")


def _mes(sessao: m.SessaoRecarga) -> str:
    return f"{MESES[sessao.inicio_em.month - 1]} de {sessao.inicio_em.year}"


def _registrar(db: Session, sessao: m.SessaoRecarga, tipo: str, titulo: str, mensagem: str,
               quando: datetime) -> m.Notificacao:
    n = m.Notificacao(
        criado_em=quando, unidade_id=sessao.unidade_id, usuario_id=sessao.usuario_id, sessao_id=sessao.id,
        tipo=tipo, titulo=titulo, mensagem=mensagem,
    )
    db.add(n)
    # falha no canal de envio não pode desfazer o encerramento: o aviso continua no app
    try:
        enviador.enviar(n, db.get(m.Usuario, sessao.usuario_id))
    except Exception:
        log.exception("Falha ao enviar o aviso da sessão #%s", sessao.id)
    return n


def _resumo(db: Session, sessao: m.SessaoRecarga) -> str:
    """Energia, duração, ociosidade e valor da sessão (sem a situação da cobrança)."""
    ociosa = ""
    if sessao.minutos_ociosos >= 1:
        ociosa = f", com {_duracao(sessao.minutos_ociosos)} de vaga ocupada após a carga"
    return (
        f"Recarga de {_quando(sessao)}: {br(sessao.kwh_entregue)} kWh em {_duracao(sessao.duracao_min)}{ociosa}. "
        f"Valor da sessão: {_reais(valor_da_sessao(db, sessao))} (energia, perdas e ociosidade; "
        f"a taxa de infraestrutura é mensal)."
    )


def notificar_resumo_sessao(db: Session, sessao: m.SessaoRecarga, motivos: list[dict],
                            quando: datetime) -> m.Notificacao:
    """Resumo da recarga encerrada: energia, duração, ociosidade, valor e situação da cobrança."""
    mensagem = _resumo(db, sessao)
    if sessao.status_cobranca == m.COBRANCA_REVISAO_IA:
        # o primeiro motivo que retém a cobrança (os de severidade baixa só avisam)
        motivo = next(mo for mo in motivos if mo["severidade"] != "baixa")
        mensagem += (f" Situação: em revisão pelo gestor: {motivo['descricao'].rstrip('.')}. "
                     f"Só entra na fatura depois da revisão.")
    else:
        mensagem += f" Situação: liberada para a fatura de {_mes(sessao)}."
    return _registrar(db, sessao, "resumo_sessao", f"Recarga encerrada: {br(sessao.kwh_entregue)} kWh",
                      mensagem, quando)


def notificar_ociosidade(db: Session, sessao: m.SessaoRecarga, quando: datetime) -> m.Notificacao:
    """Aviso extra quando o carro ficou muito tempo na vaga depois da carga completa."""
    params = parametros_do_mes(db, sessao.inicio_em.strftime("%Y-%m"))
    mensagem = f"Seu veículo ficou {br(sessao.minutos_ociosos / 60)} h conectado depois da carga completa. "
    if params.ociosidade_ativa:
        r = calcular_rateio(
            [SessaoRateio(sessao_id=sessao.id, kwh=sessao.kwh_entregue, minutos_ociosos=sessao.minutos_ociosos)], params
        )
        mensagem += (f"A tolerância é de {params.tolerancia_ociosidade_min:.0f} min e a taxa de ociosidade "
                     f"desta recarga foi {_reais(float(r.valor_ociosidade))}. ")
    else:
        mensagem += "A taxa de ociosidade está desligada. "
    mensagem += "Libere a vaga ao fim da carga para outros moradores."
    return _registrar(db, sessao, "ociosidade", "Vaga ocupada após a carga completa", mensagem, quando)


def notificar_revisao(db: Session, sessao: m.SessaoRecarga, decisao: str, ajuste_valor: float,
                      observacao: str, da_quarentena: bool, quando: datetime) -> m.Notificacao:
    """Decisão do gestor sobre uma sessão retida pela IA ou atribuída à unidade (quarentena)."""
    obs = f" ({observacao})" if observacao else ""
    credito = f" com crédito de {_reais(ajuste_valor)}" if ajuste_valor > 0 else ""
    if decisao == "descartar":
        titulo = "Recarga não será cobrada"
        mensagem = f"A recarga de {_quando(sessao)} foi analisada pelo gestor e não será cobrada{obs}."
    elif da_quarentena:
        # o morador não recebeu o resumo no encerramento (o cartão não foi reconhecido)
        titulo = "Recarga atribuída à sua unidade"
        mensagem = (f"{_resumo(db, sessao)} O gestor atribuiu esta recarga à sua unidade{obs}; "
                    f"ela entra na fatura de {_mes(sessao)}{credito}.")
    else:
        titulo = f"Recarga liberada{credito}"
        mensagem = (f"A recarga de {_quando(sessao)} foi analisada pelo gestor{obs} e entra na fatura de "
                    f"{_mes(sessao)}{credito}.")
    return _registrar(db, sessao, "revisao", titulo, mensagem, quando)

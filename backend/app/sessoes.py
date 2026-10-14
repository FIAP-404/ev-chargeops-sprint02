"""Ciclo de vida de uma sessão de recarga.

Segue o fluxo da seção 5.2 da Sprint 01:
  1. usuário se identifica por RFID          -> iniciar_sessao()
  2. plataforma valida o usuário
  3. sessão aberta com leitura inicial
  4. carregador envia telemetria             -> registrar_telemetria()
  5. dados normalizados e gravados como eventos
  6. sessão encerrada com kWh entregues      -> encerrar_sessao()
  7/8. IA avalia a sessão e decide se ela vai direto para o rateio
       ou fica retida para revisão do gestor -> app.ia.servico
  ...
  gestor decide sobre sessões retidas        -> revisar_sessao()

Tanto a API quanto o simulador chamam estas funções, então o dado simulado
percorre exatamente o mesmo caminho que um dado real percorreria.
"""
import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.faturamento import marcar_fatura_desatualizada, valor_da_sessao
from app.ia import servico as ia
from app.ia.anomalias import br


class ErroSessao(Exception):
    """Erro de regra de negócio (vira HTTP 4xx na API)."""

    def __init__(self, mensagem: str, status_http: int = 400):
        super().__init__(mensagem)
        self.status_http = status_http


# motivos de encerramento aceitos e o status_final que cada um gera
MOTIVOS_ENCERRAMENTO = {
    "usuario_desconectou",
    "carga_completa",
    "limite_operacional",
    "falha_carregador",
    "falha_comunicacao",
}


def hash_rfid(codigo: str) -> str:
    """O código do cartão nunca é salvo em texto puro (seção 5.6: codigo_rfid_hash)."""
    return hashlib.sha256(codigo.strip().upper().encode()).hexdigest()


def para_hora_local(dt: datetime | None) -> datetime | None:
    """Datas com fuso (ex.: "...Z" ou "...-03:00") viram horário local sem fuso,
    o mesmo formato de datetime.now() usado no resto do sistema."""
    return dt.astimezone().replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


def _evento(db: Session, sessao: m.SessaoRecarga, ts: datetime, tipo: str, payload: dict) -> None:
    db.add(m.EventoSessao(sessao_id=sessao.id, timestamp=ts, tipo_evento=tipo, payload_json=json.dumps(payload)))


def sessao_aberta_do_carregador(db: Session, carregador_id: str) -> m.SessaoRecarga | None:
    return db.scalar(
        select(m.SessaoRecarga).where(
            m.SessaoRecarga.carregador_id == carregador_id,
            m.SessaoRecarga.status_final == m.STATUS_EM_ANDAMENTO,
        )
    )


def registrar_tentativa_ocupado(db: Session, carregador_id: str, usuario_id: int | None, quando: datetime) -> None:
    """Guarda a demanda que não foi atendida porque o carregador estava em uso."""
    db.add(m.TentativaOcupado(carregador_id=carregador_id, usuario_id=usuario_id, timestamp=quando))
    db.commit()


def iniciar_sessao(
    db: Session,
    carregador_id: str,
    codigo_rfid: str,
    inicio_em: datetime | None = None,
    leitura_inicial_kwh: float | None = None,
) -> m.SessaoRecarga:
    """leitura_inicial_kwh vazia = última leitura do medidor deste carregador."""
    inicio_em = para_hora_local(inicio_em) or datetime.now()
    carregador = db.get(m.Carregador, carregador_id)
    if carregador is None:
        raise ErroSessao(f"Carregador '{carregador_id}' não encontrado", 404)

    rfid = hash_rfid(codigo_rfid)
    credencial = db.scalar(select(m.CredencialRFID).where(m.CredencialRFID.codigo_rfid_hash == rfid))
    usuario = db.get(m.Usuario, credencial.usuario_id) if credencial and credencial.status == "ativo" else None
    if usuario is not None and usuario.status != "ativo":
        usuario = None

    # um carregador só atende uma sessão por vez; a tentativa vira dado de demanda
    if sessao_aberta_do_carregador(db, carregador_id) is not None:
        registrar_tentativa_ocupado(db, carregador_id, usuario.id if usuario else None, inicio_em)
        raise ErroSessao("Carregador ocupado: já existe uma sessão em andamento", 409)

    # a sessão começa depois da anterior e o medidor continua de onde ela parou
    ultima = db.scalar(
        select(m.SessaoRecarga)
        .where(m.SessaoRecarga.carregador_id == carregador_id)
        .order_by(m.SessaoRecarga.inicio_em.desc())
        .limit(1)
    )
    if ultima is not None and ultima.fim_em > inicio_em:
        raise ErroSessao("inicio_em se sobrepõe a uma sessão já encerrada neste carregador", 422)
    leitura_anterior = ultima.leitura_final_kwh if ultima is not None else 0.0
    if leitura_inicial_kwh is None:
        leitura_inicial_kwh = leitura_anterior
    elif leitura_inicial_kwh < leitura_anterior:
        raise ErroSessao(
            f"leitura_inicial_kwh menor que a última leitura do medidor ({br(leitura_anterior, 3)} kWh)", 422
        )

    sessao = m.SessaoRecarga(
        carregador_id=carregador_id,
        rfid_hash=rfid,
        usuario_id=usuario.id if usuario else None,
        unidade_id=usuario.unidade_id if usuario else None,
        veiculo_id=credencial.veiculo_id if (credencial and usuario) else None,
        inicio_em=inicio_em,
        leitura_inicial_kwh=leitura_inicial_kwh,
        leitura_final_kwh=leitura_inicial_kwh,
        status_final=m.STATUS_EM_ANDAMENTO,
        status_cobranca=m.COBRANCA_AGUARDANDO,
    )
    db.add(sessao)
    db.flush()  # gera o id
    _evento(
        db,
        sessao,
        inicio_em,
        "inicio",
        {"identificado": usuario is not None, "leitura_inicial_kwh": leitura_inicial_kwh},
    )
    carregador.status = "em_uso"
    db.commit()
    return sessao


def registrar_telemetria(
    db: Session,
    sessao_id: int,
    timestamp: datetime,
    potencia_kw: float,
    energia_acumulada_kwh: float,
    status_carregador: str = "carregando",
) -> m.SessaoRecarga:
    """Grava uma leitura do carregador.

    energia_acumulada_kwh é a leitura do medidor (contador que só cresce), como
    o HCA G2/SEMS reporta. O kWh da sessão é calculado pela diferença entre a
    leitura final e a inicial.
    """
    timestamp = para_hora_local(timestamp)
    sessao = _sessao_aberta(db, sessao_id)
    if timestamp < sessao.inicio_em:
        raise ErroSessao("timestamp da telemetria anterior ao início da sessão", 422)

    if energia_acumulada_kwh < (sessao.leitura_final_kwh or 0):
        # medidor não pode andar para trás: registra o problema e ignora a leitura
        _evento(db, sessao, timestamp, "erro", {"erro": "leitura_regressiva", "valor": energia_acumulada_kwh})
        db.commit()
        return sessao

    sessao.leitura_final_kwh = energia_acumulada_kwh  # último dado válido
    sessao.potencia_max_kw = max(sessao.potencia_max_kw or 0, potencia_kw)

    tipo = "telemetria"
    if status_carregador == "carga_completa" and sessao.carga_completa_em is None:
        sessao.carga_completa_em = timestamp  # a partir daqui o tempo conta como ocioso
        tipo = "carga_completa"
    elif status_carregador == "erro":
        tipo = "erro"

    _evento(
        db,
        sessao,
        timestamp,
        tipo,
        {"potencia_kw": potencia_kw, "energia_acumulada_kwh": energia_acumulada_kwh, "status": status_carregador},
    )
    db.commit()
    return sessao


def encerrar_sessao(
    db: Session,
    sessao_id: int,
    motivo: str,
    fim_em: datetime | None = None,
    leitura_final_kwh: float | None = None,
) -> m.SessaoRecarga:
    if motivo not in MOTIVOS_ENCERRAMENTO:
        raise ErroSessao(f"Motivo inválido. Use um destes: {sorted(MOTIVOS_ENCERRAMENTO)}")

    sessao = _sessao_aberta(db, sessao_id)
    fim_em = para_hora_local(fim_em) or datetime.now()
    if fim_em < sessao.inicio_em:
        raise ErroSessao("fim_em anterior ao início da sessão")
    ultimo_evento = db.scalar(select(func.max(m.EventoSessao.timestamp)).where(m.EventoSessao.sessao_id == sessao.id))
    if ultimo_evento is not None and fim_em < ultimo_evento:
        raise ErroSessao("fim_em anterior à última telemetria da sessão", 422)
    if leitura_final_kwh is not None and leitura_final_kwh < sessao.leitura_final_kwh:
        raise ErroSessao(
            f"leitura_final_kwh menor que a última telemetria ({br(sessao.leitura_final_kwh, 3)} kWh)", 422
        )

    # Falha de comunicação: sem leitura final, usa o último dado válido
    # recebido na telemetria (Sprint 01, seção 5.4).
    if leitura_final_kwh is None:
        leitura_final_kwh = sessao.leitura_final_kwh
        _evento(db, sessao, fim_em, "leitura_reconciliada", {"leitura_usada_kwh": leitura_final_kwh})
    sessao.leitura_final_kwh = leitura_final_kwh

    sessao.fim_em = fim_em
    sessao.motivo_encerramento = motivo
    sessao.kwh_entregue = round(max(leitura_final_kwh - sessao.leitura_inicial_kwh, 0.0), 3)
    sessao.duracao_min = round((fim_em - sessao.inicio_em).total_seconds() / 60, 1)

    # tempo carregando x tempo ocioso (conectado depois da carga completa)
    fim_da_carga = sessao.carga_completa_em or fim_em
    horas_carregando = max((fim_da_carga - sessao.inicio_em).total_seconds() / 3600, 1 / 60)
    sessao.minutos_ociosos = round((fim_em - fim_da_carga).total_seconds() / 60, 1)
    sessao.potencia_media_kw = round(sessao.kwh_entregue / horas_carregando, 2)

    if motivo in ("falha_carregador", "falha_comunicacao"):
        sessao.status_final = m.STATUS_FALHA
    elif sessao.carga_completa_em is not None or motivo == "carga_completa":
        sessao.status_final = m.STATUS_CONCLUIDA
    else:
        # sessão interrompida: cobra só o kWh medido (Sprint 01, seção 5.4)
        sessao.status_final = m.STATUS_INTERROMPIDA

    _evento(
        db,
        sessao,
        fim_em,
        "fim",
        {"motivo": motivo, "kwh_entregue": sessao.kwh_entregue, "minutos_ociosos": sessao.minutos_ociosos},
    )

    carregador = db.get(m.Carregador, sessao.carregador_id)
    carregador.status = "disponivel"

    # --- decisão de cobrança ---------------------------------------------
    if sessao.usuario_id is None:
        # Sprint 01: usuário sem identificação -> quarentena até o gestor analisar
        sessao.status_cobranca = m.COBRANCA_QUARENTENA
        db.add(
            m.AlertaIA(
                criado_em=fim_em,
                tipo="sessao_sem_identificacao",
                severidade="media",
                sessao_id=sessao.id,
                titulo=f"Sessão #{sessao.id} sem usuário identificado",
                mensagem=(
                    f"RFID não cadastrado ou inativo. {br(sessao.kwh_entregue)} kWh entregues "
                    f"em {br(sessao.duracao_min / 60)} h."
                ),
                acao_sugerida="Identificar o responsável e atribuir a sessão a uma unidade antes de cobrar.",
            )
        )
    else:
        # A IA decide se a sessão segue direto para o rateio ou fica retida.
        avaliacao = ia.avaliar_sessao_encerrada(db, sessao)
        sessao.score_anomalia = avaliacao.score
        sessao.anomalia_motivos = json.dumps(avaliacao.motivos, ensure_ascii=False)
        sessao.status_cobranca = (
            m.COBRANCA_REVISAO_IA if avaliacao.reter_para_revisao else m.COBRANCA_LIBERADA
        )
        if avaliacao.motivos:
            # motivos de severidade baixa só geram aviso; média/alta também retêm a cobrança
            db.add(ia.alerta_da_avaliacao(sessao, avaliacao, fim_em))

    # sessão aberta no fechamento do mês: a fatura já gerada ficou sem ela
    marcar_fatura_desatualizada(db, sessao)
    db.commit()
    return sessao


def revisar_sessao(
    db: Session,
    sessao_id: int,
    decisao: str,
    usuario_id: int | None = None,
    ajuste_valor: float = 0.0,
    observacao: str = "",
) -> m.SessaoRecarga:
    """Decisão do gestor sobre uma sessão retida (quarentena ou revisão da IA).

    decisao = "liberar"   -> entra no rateio (opcionalmente com crédito/ajuste)
    decisao = "descartar" -> não será cobrada
    """
    sessao = db.get(m.SessaoRecarga, sessao_id)
    if sessao is None:
        raise ErroSessao("Sessão não encontrada", 404)
    if sessao.status_cobranca not in (m.COBRANCA_QUARENTENA, m.COBRANCA_REVISAO_IA):
        raise ErroSessao("Só sessões em quarentena ou em revisão podem ser revisadas", 409)
    if decisao not in ("liberar", "descartar"):
        raise ErroSessao("decisao deve ser 'liberar' ou 'descartar'")
    if ajuste_valor < 0:
        raise ErroSessao("ajuste_valor é um crédito e deve ser >= 0")

    if decisao == "liberar":
        teto = valor_da_sessao(db, sessao)
        if ajuste_valor > teto:
            raise ErroSessao(f"O crédito não pode passar do valor da sessão (R$ {teto:.2f})".replace(".", ","))
        if sessao.usuario_id is None:
            if usuario_id is None:
                raise ErroSessao("Informe usuario_id para atribuir a sessão sem identificação")
            usuario = db.get(m.Usuario, usuario_id)
            if usuario is None or usuario.unidade_id is None:
                raise ErroSessao("Usuário inválido ou sem unidade", 404)
            sessao.usuario_id = usuario.id
            sessao.unidade_id = usuario.unidade_id
        sessao.status_cobranca = m.COBRANCA_LIBERADA
        sessao.ajuste_valor = round(ajuste_valor, 2)
        sessao.ajuste_motivo = observacao or None
    else:
        sessao.status_cobranca = m.COBRANCA_DESCARTADA
        sessao.ajuste_motivo = observacao or None

    _evento(
        db,
        sessao,
        datetime.now(),
        "revisao",
        {"decisao": decisao, "ajuste_valor": ajuste_valor, "observacao": observacao, "usuario_id": usuario_id},
    )
    for alerta in db.scalars(
        select(m.AlertaIA).where(m.AlertaIA.sessao_id == sessao.id, m.AlertaIA.status == "aberto")
    ):
        alerta.status = "resolvido"
        alerta.resolucao = f"{decisao}: {observacao}".strip(": ")
    marcar_fatura_desatualizada(db, sessao)
    db.commit()
    return sessao


def _sessao_aberta(db: Session, sessao_id: int) -> m.SessaoRecarga:
    sessao = db.get(m.SessaoRecarga, sessao_id)
    if sessao is None:
        raise ErroSessao("Sessão não encontrada", 404)
    if sessao.status_final != m.STATUS_EM_ANDAMENTO:
        raise ErroSessao("Sessão já encerrada", 409)
    return sessao

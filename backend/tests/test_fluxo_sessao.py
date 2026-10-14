"""Testes do fluxo completo: RFID -> telemetria -> encerramento -> IA -> fatura."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import models as m
from app import notificacoes
from app.faturamento import gerar_faturas, valor_da_sessao
from app.seed import CARREGADOR_ID, QR_TOKEN_CARREGADOR
from app.sessoes import (
    ErroSessao,
    encerrar_sessao,
    iniciar_sessao,
    iniciar_sessao_app,
    registrar_telemetria,
    revisar_sessao,
)

T0 = datetime(2026, 8, 3, 19, 10)


def recarga(db, rfid, kwh, inicio=T0, horas_carga=3.0, ociosas_h=0.0, potencia=6.2, motivo="usuario_desconectou",
            leitura_inicial=1000.0):
    """Executa uma sessão completa enviando telemetria a cada 30 min."""
    s = iniciar_sessao(db, CARREGADOR_ID, rfid, inicio, leitura_inicial)
    passos = max(int(horas_carga * 2), 1)
    for i in range(1, passos + 1):
        registrar_telemetria(db, s.id, inicio + timedelta(minutes=30 * i), potencia,
                             leitura_inicial + kwh * i / passos, "carregando")
    fim_carga = inicio + timedelta(hours=horas_carga)
    if ociosas_h:
        registrar_telemetria(db, s.id, fim_carga, 0.0, leitura_inicial + kwh, "carga_completa")
    return encerrar_sessao(db, s.id, motivo, fim_carga + timedelta(hours=ociosas_h))


def usuario(db, email):
    return db.scalar(select(m.Usuario).where(m.Usuario.email == email))


def recarga_app(db, email, veiculo_id=None, kwh=18.6, inicio=T0, leitura_inicial=1000.0):
    """Mesma recarga, mas iniciada pelo app lendo o QR Code do carregador."""
    s = iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, usuario(db, email).id, veiculo_id, inicio,
                           leitura_inicial)
    registrar_telemetria(db, s.id, inicio + timedelta(hours=3), 6.2, leitura_inicial + kwh, "carregando")
    return encerrar_sessao(db, s.id, "usuario_desconectou", inicio + timedelta(hours=3))


def avisos(db, sessao_id, tipo=None):
    q = select(m.Notificacao).where(m.Notificacao.sessao_id == sessao_id)
    if tipo:
        q = q.where(m.Notificacao.tipo == tipo)
    return db.scalars(q).all()


def test_sessao_identificada_normal_vai_para_rateio(db):
    s = recarga(db, "RFID-A1204-01", 18.6)
    assert s.kwh_entregue == pytest.approx(18.6)
    assert s.usuario_id is not None and s.unidade_id is not None
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert s.metodo_identificacao == "rfid"
    tipos = [e.tipo_evento for e in s.eventos]
    assert tipos[0] == "inicio" and tipos[-1] == "fim"


def test_sessao_por_qr_entra_no_rateio(db):
    rafael = usuario(db, "rafael.a302@exemplo.com")
    s = recarga_app(db, "rafael.a302@exemplo.com")  # sem veiculo_id: Rafael só tem o BYD Seal
    assert s.veiculo_id == rafael.veiculos[0].id and s.unidade_id == rafael.unidade_id
    assert s.metodo_identificacao == "app_qr" and s.rfid_hash is None
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert json.loads(s.eventos[0].payload_json)["metodo"] == "app_qr"
    assert len(avisos(db, s.id, "resumo_sessao")) == 1  # o resumo chega igual ao do RFID
    f = {x.unidade.rotulo: x for x in gerar_faturas(db, "2026-08")}["A-302"]
    assert f.qtd_sessoes == 1


def test_qr_token_invalido_recusa_com_403(db):
    rafael = usuario(db, "rafael.a302@exemplo.com")
    with pytest.raises(ErroSessao) as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, "QR-COPIADO-ERRADO", rafael.id, None, T0, 1000)
    assert erro.value.status_http == 403
    assert db.scalar(select(m.SessaoRecarga)) is None
    assert db.scalar(select(m.TentativaOcupado)) is None


def test_qr_usuario_inativo_recusa_com_403(db):
    rafael = usuario(db, "rafael.a302@exemplo.com")
    rafael.status = "inativo"
    db.commit()
    with pytest.raises(ErroSessao, match="inativo") as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, rafael.id, None, T0, 1000)
    assert erro.value.status_http == 403


def test_qr_veiculo_de_outro_usuario_recusa_com_422(db):
    rafael = usuario(db, "rafael.a302@exemplo.com")
    carro_da_marina = usuario(db, "marina.a1204@exemplo.com").veiculos[0]
    with pytest.raises(ErroSessao, match="não pertence") as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, rafael.id, carro_da_marina.id, T0, 1000)
    assert erro.value.status_http == 422


def test_qr_usuario_com_dois_veiculos_precisa_escolher(db):
    marina = usuario(db, "marina.a1204@exemplo.com")
    with pytest.raises(ErroSessao, match="mais de um veículo") as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, marina.id, None, T0, 1000)
    assert erro.value.status_http == 422
    s = recarga_app(db, "marina.a1204@exemplo.com", veiculo_id=marina.veiculos[1].id)
    assert s.veiculo_id == marina.veiculos[1].id


def test_qr_usuario_inexistente_recusa_com_404(db):
    with pytest.raises(ErroSessao, match="Usuário não encontrado") as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, 9999, None, T0, 1000)
    assert erro.value.status_http == 404


def test_qr_usuario_sem_veiculo_recusa_com_422(db):
    otavio = usuario(db, "otavio.b202@exemplo.com")  # B-202 não tem carro elétrico
    with pytest.raises(ErroSessao, match="sem veículo") as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, otavio.id, None, T0, 1000)
    assert erro.value.status_http == 422


def test_qr_com_carregador_ocupado_recusa_e_registra_tentativa(db):
    iniciar_sessao(db, CARREGADOR_ID, "RFID-A0302-01", T0, 500)
    juliana = usuario(db, "juliana.a507@exemplo.com")
    with pytest.raises(ErroSessao) as erro:
        iniciar_sessao_app(db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, juliana.id, None, T0 + timedelta(minutes=10))
    assert erro.value.status_http == 409
    assert db.scalar(select(m.TentativaOcupado)).usuario_id == juliana.id


def test_rfid_desconhecido_vai_para_quarentena_e_gera_alerta(db):
    s = recarga(db, "CARTAO-NAO-CADASTRADO", 10)
    assert s.usuario_id is None
    assert s.status_cobranca == m.COBRANCA_QUARENTENA
    alerta = db.scalar(select(m.AlertaIA).where(m.AlertaIA.sessao_id == s.id))
    assert alerta.tipo == "sessao_sem_identificacao"


def test_gestor_atribui_sessao_da_quarentena(db):
    s = recarga(db, "CARTAO-NAO-CADASTRADO", 10)
    usuario = db.scalar(select(m.Usuario).where(m.Usuario.email == "rafael.a302@exemplo.com"))
    revisar_sessao(db, s.id, "liberar", usuario_id=usuario.id, observacao="cartão provisório")
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert s.unidade_id == usuario.unidade_id


def test_carregador_ocupado_recusa_e_registra_demanda(db):
    iniciar_sessao(db, CARREGADOR_ID, "RFID-A0302-01", T0, 500)
    with pytest.raises(ErroSessao) as erro:
        iniciar_sessao(db, CARREGADOR_ID, "RFID-A0507-01", T0 + timedelta(minutes=10), 500)
    assert erro.value.status_http == 409
    assert db.scalar(select(m.TentativaOcupado)).usuario_id is not None


def test_ociosidade_calculada_a_partir_da_carga_completa(db):
    s = recarga(db, "RFID-A0302-01", 20, horas_carga=3, ociosas_h=1.5)
    assert s.minutos_ociosos == pytest.approx(90)
    assert s.status_final == m.STATUS_CONCLUIDA


def test_falha_de_comunicacao_usa_ultima_leitura_valida(db):
    s = iniciar_sessao(db, CARREGADOR_ID, "RFID-A0507-01", T0, 2000)
    registrar_telemetria(db, s.id, T0 + timedelta(hours=1), 6.0, 2006.0)
    # leitura regressiva é ignorada
    registrar_telemetria(db, s.id, T0 + timedelta(hours=1, minutes=5), 6.0, 1990.0)
    s = encerrar_sessao(db, s.id, "falha_comunicacao", T0 + timedelta(hours=2), leitura_final_kwh=None)
    assert s.kwh_entregue == pytest.approx(6.0)
    assert s.status_final == m.STATUS_FALHA
    # falha vai para revisão para o gestor decidir sobre crédito
    assert s.status_cobranca == m.COBRANCA_REVISAO_IA


def test_ia_retem_kwh_acima_da_bateria(db):
    # Renault Kwid tem bateria de 26,8 kWh
    s = recarga(db, "RFID-A1204-02", 40, horas_carga=6)
    assert s.status_cobranca == m.COBRANCA_REVISAO_IA
    assert "kwh_acima_da_bateria" in s.anomalia_motivos
    alerta = db.scalar(select(m.AlertaIA).where(m.AlertaIA.sessao_id == s.id))
    assert alerta.severidade == "alta"


def test_ociosidade_longa_avisa_mas_nao_retem(db):
    s = recarga(db, "RFID-A0811-01", 25, horas_carga=4, ociosas_h=5)
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    alerta = db.scalar(select(m.AlertaIA).where(m.AlertaIA.sessao_id == s.id))
    assert alerta is not None and alerta.severidade == "baixa"
    # além do alerta ao gestor, o morador recebe o aviso de ociosidade
    [aviso] = avisos(db, s.id, "ociosidade")
    assert aviso.usuario_id == s.usuario_id
    assert "5,0 h conectado" in aviso.mensagem and "R$ 27,00" in aviso.mensagem


def test_encerramento_gera_resumo_para_o_morador(db):
    s = recarga(db, "RFID-A1204-01", 18.6)
    [aviso] = avisos(db, s.id)
    assert aviso.tipo == "resumo_sessao" and aviso.usuario_id == s.usuario_id
    assert "18,6 kWh" in aviso.mensagem and "liberada" in aviso.mensagem
    assert aviso.lida is False


def test_sessao_sem_identificacao_nao_notifica(db):
    s = recarga(db, "CARTAO-NAO-CADASTRADO", 10)
    assert avisos(db, s.id) == []


def test_sessao_retida_avisa_que_esta_em_revisao(db):
    s = recarga(db, "RFID-A1204-02", 40, horas_carga=6)
    [aviso] = avisos(db, s.id)
    assert "em revisão" in aviso.mensagem and "bateria" in aviso.mensagem


def test_falha_no_envio_nao_impede_o_encerramento(db):
    class EnviadorQuebrado:
        def enviar(self, n, usuario):
            raise ConnectionError("provedor fora do ar")

    notificacoes.usar_enviador(EnviadorQuebrado())
    try:
        s = recarga(db, "RFID-A1204-01", 18.6)
    finally:
        notificacoes.usar_enviador(notificacoes.EnviadorConsole())
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert len(avisos(db, s.id)) == 1  # o aviso continua na caixa do app


def test_sessao_retida_nao_entra_na_fatura_ate_revisao(db):
    normal = recarga(db, "RFID-A1204-01", 18.6, inicio=datetime(2026, 8, 3, 19))
    suspeita = recarga(db, "RFID-A1204-02", 40, inicio=datetime(2026, 8, 10, 19), horas_carga=6,
                       leitura_inicial=1018.6)
    faturas = {f.unidade.rotulo: f for f in gerar_faturas(db, "2026-08")}
    f = faturas["A-1204"]
    assert f.kwh_total == pytest.approx(18.6)
    assert f.sessoes_pendentes == 1
    assert f.status == "fechada_com_pendencias"

    # gestor confere, libera a sessão e regera a fatura
    revisar_sessao(db, suspeita.id, "liberar", observacao="leitura conferida")
    f = {x.unidade.rotulo: x for x in gerar_faturas(db, "2026-08")}["A-1204"]
    assert f.kwh_total == pytest.approx(58.6)
    assert f.sessoes_pendentes == 0
    assert normal.status_cobranca == m.COBRANCA_FATURADA


def test_credito_limitado_ao_valor_da_sessao(db):
    # 40 kWh × R$ 0,95 = R$ 38,00 + 5% de perdas = R$ 39,90 (sem ociosidade; taxa mensal fica de fora)
    s = recarga(db, "RFID-A1204-02", 40, horas_carga=6)
    assert valor_da_sessao(db, s) == pytest.approx(39.90)
    with pytest.raises(ErroSessao, match="valor da sessão"):
        revisar_sessao(db, s.id, "liberar", ajuste_valor=39.91)
    assert s.status_cobranca == m.COBRANCA_REVISAO_IA

    revisar_sessao(db, s.id, "liberar", ajuste_valor=39.90, observacao="crédito integral")
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert s.ajuste_valor == pytest.approx(39.90)
    [aviso] = avisos(db, s.id, "revisao")
    assert aviso.titulo == "Recarga liberada com crédito de R$ 39,90"


def test_sessao_descartada_avisa_que_nao_sera_cobrada(db):
    s = recarga(db, "RFID-A1204-02", 40, horas_carga=6)
    decisao = datetime(2026, 9, 1)
    revisar_sessao(db, s.id, "descartar", observacao="erro de medição confirmado", agora=decisao)
    [aviso] = avisos(db, s.id, "revisao")
    assert aviso.titulo == "Recarga não será cobrada" and "erro de medição confirmado" in aviso.mensagem
    # o aviso leva a data da decisão (no simulador, a virada do mês), igual ao evento da revisão
    revisao = db.scalar(
        select(m.EventoSessao).where(m.EventoSessao.sessao_id == s.id, m.EventoSessao.tipo_evento == "revisao")
    )
    assert aviso.criado_em == decisao and revisao.timestamp == decisao


def test_quarentena_atribuida_envia_resumo_ao_morador(db):
    s = recarga(db, "CARTAO-NAO-CADASTRADO", 10)
    usuario = db.scalar(select(m.Usuario).where(m.Usuario.email == "rafael.a302@exemplo.com"))
    revisar_sessao(db, s.id, "liberar", usuario_id=usuario.id, observacao="visitante do A-302")
    [aviso] = avisos(db, s.id)
    assert aviso.usuario_id == usuario.id and aviso.titulo == "Recarga atribuída à sua unidade"
    # 10 kWh × R$ 0,95 = R$ 9,50 + 5% de perdas = R$ 9,98
    assert "10,0 kWh" in aviso.mensagem and "R$ 9,98" in aviso.mensagem
    assert "fatura de agosto de 2026" in aviso.mensagem


def test_fatura_do_exemplo_sprint01_via_fluxo_completo(db):
    """Unidade A-1204 consome 42 kWh no mês com dois veículos -> R$ 61,90."""
    params = db.get(m.ParametrosRateio, 1)
    params.ociosidade_ativa = False
    db.commit()
    recarga(db, "RFID-A1204-01", 18.6, inicio=datetime(2026, 8, 3, 19, 10))
    recarga(db, "RFID-A1204-02", 23.4, inicio=datetime(2026, 8, 12, 20, 0), horas_carga=4, leitura_inicial=1018.6)
    f = {x.unidade.rotulo: x for x in gerar_faturas(db, "2026-08")}
    assert f["A-1204"].valor_total == pytest.approx(61.90)
    assert f["B-202"].valor_total == 0  # unidade sem uso

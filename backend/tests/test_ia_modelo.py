"""Testes do caminho do Isolation Forest (com modelo treinado) e do treino na subida da API."""
import json
import random
from datetime import datetime, timedelta

from sqlalchemy import select

from app import models as m
from app.ia import servico as ia
from app.seed import CARREGADOR_ID

from .test_fluxo_sessao import T0, recarga


def historico(db, n=60, achatadas=True):
    """Insere n sessões normais já liberadas (maio a julho de 2026), sem passar pela IA.

    Devolve a última leitura do medidor, de onde a próxima sessão continua.

    achatadas=True inclui recargas com potência quase constante (média = máxima);
    False deixa só curvas com pico de 5% a 15% acima da média.
    """
    rng = random.Random(0)
    veiculos = db.scalars(select(m.Veiculo)).all()
    # uma sessão por dia, às 18 h: sem sobreposição no carregador
    dias = sorted(rng.sample(range(91), n))
    leitura = 0.0
    for dia in dias:
        v = rng.choice(veiculos)
        esp = min(v.potencia_ac_max_kw, 7.4)
        media = esp * rng.uniform(0.85, 0.99)
        pico = media / rng.uniform(0.85, 1.0) if achatadas else media / rng.uniform(0.85, 0.95)
        kwh = rng.uniform(10, 30)  # 19,2 kWh fica dentro do perfil do usuário
        ociosos = rng.uniform(0, 60)
        duracao_min = kwh / media * 60 + ociosos
        inicio = datetime(2026, 5, 1, 18) + timedelta(days=dia)
        db.add(m.SessaoRecarga(
            usuario_id=v.usuario_id, unidade_id=v.usuario.unidade_id, veiculo_id=v.id, carregador_id=CARREGADOR_ID,
            inicio_em=inicio, fim_em=inicio + timedelta(minutes=duracao_min),
            leitura_inicial_kwh=leitura, leitura_final_kwh=leitura + kwh,
            kwh_entregue=kwh, duracao_min=duracao_min, minutos_ociosos=ociosos,
            potencia_media_kw=media, potencia_max_kw=pico,
            status_final=m.STATUS_CONCLUIDA, status_cobranca=m.COBRANCA_LIBERADA,
        ))
        leitura += kwh
    db.commit()
    return leitura


def test_isolation_forest_retem_cabo_degradado(db):
    leitura = historico(db)
    ia.treinar_modelos(db, T0)
    assert ia.detector().treinado
    # cabo com defeito: 1,2 kW num Dolphin que carrega a 6,6 kW, com kWh e duração plausíveis (nenhuma regra pega)
    s = recarga(db, "RFID-A1204-01", 6.0, horas_carga=5, potencia=1.2, leitura_inicial=leitura)
    assert s.status_cobranca == m.COBRANCA_REVISAO_IA
    motivos = json.loads(s.anomalia_motivos)
    assert [(mo["origem"], mo["codigo"]) for mo in motivos] == [("modelo", "padrao_atipico")]


def test_potencia_constante_normal_fica_liberada(db):
    leitura = historico(db)
    ia.treinar_modelos(db, T0)
    s = recarga(db, "RFID-A1204-01", 19.2, horas_carga=3, potencia=6.4, leitura_inicial=leitura)
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert s.score_anomalia is not None  # o modelo avaliou a sessão


def test_curva_achatada_com_potencia_saudavel_nao_retem(db):
    # histórico só com curvas que têm pico acima da média: potência constante fica fora do padrão
    leitura = historico(db, achatadas=False)
    ia.treinar_modelos(db, T0)
    # 5,4 kW constantes num Dolphin de 6,6 kW (82% do esperado): o modelo acha atípica, mas não é defeito
    s = recarga(db, "RFID-A1204-01", 18.9, horas_carga=3.5, potencia=5.4, leitura_inicial=leitura)
    assert s.score_anomalia >= 0.97
    assert s.status_cobranca == m.COBRANCA_LIBERADA
    assert json.loads(s.anomalia_motivos) == []


def test_media_acima_do_pico_continua_retida(db):
    leitura = historico(db, achatadas=False)
    ia.treinar_modelos(db, T0)
    # média 105% do esperado com pico em 90%: leitura inconsistente, a proteção não vale
    s = recarga(db, "RFID-A1204-01", 20.8, horas_carga=3, potencia=5.94, leitura_inicial=leitura)
    assert s.potencia_media_kw > s.potencia_max_kw
    assert s.status_cobranca == m.COBRANCA_REVISAO_IA
    assert [mo["codigo"] for mo in json.loads(s.anomalia_motivos)] == ["padrao_atipico"]


def test_treino_na_inicializacao(db):
    # só o cadastro: abaixo do mínimo de sessões, a API sobe em cold start
    assert ia.treinar_se_necessario(db) is None
    assert not ia.ARQ_DETECTOR.exists()

    historico(db)
    info = ia.treinar_se_necessario(db)
    assert info["anomalias"]["treinado"] and ia.ARQ_DETECTOR.exists()
    assert ia.detector().treinado
    # com o detector salvo, a próxima subida não treina de novo (mesmo sem o previsor)
    ia.ARQ_PREVISOR.unlink(missing_ok=True)
    assert ia.treinar_se_necessario(db) is None


def test_previsao_treina_com_semana_sem_chegadas(db):
    # histórico de maio a julho visto em outubro: as 4 semanas de teste não têm chegadas
    historico(db)
    info = ia.treinar_modelos(db, datetime(2026, 10, 13, 15))
    assert info["demanda"]["treinado"]
    assert info["demanda"]["erro_semanal_medio_pct_modelo"] is None
    assert ia.previsao_capacidade(db, datetime(2026, 10, 13, 15))["disponivel"]

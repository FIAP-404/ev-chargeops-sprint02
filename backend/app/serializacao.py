"""Conversão dos objetos do banco para JSON da API."""
import json

from app import models as m


def sessao_dict(s: m.SessaoRecarga, com_eventos: bool = False) -> dict:
    dados = {
        "id": s.id,
        "usuario_id": s.usuario_id,
        "usuario": s.usuario.nome if s.usuario else None,
        "unidade_id": s.unidade_id,
        "unidade": s.unidade.rotulo if s.unidade else None,
        "veiculo": f"{s.veiculo.modelo} ({s.veiculo.placa})" if s.veiculo else None,
        "carregador_id": s.carregador_id,
        "inicio_em": s.inicio_em.isoformat(timespec="minutes"),
        "fim_em": s.fim_em.isoformat(timespec="minutes") if s.fim_em else None,
        "leitura_inicial_kwh": s.leitura_inicial_kwh,
        "kwh_entregue": s.kwh_entregue,
        "duracao_min": s.duracao_min,
        "minutos_ociosos": s.minutos_ociosos,
        "potencia_media_kw": s.potencia_media_kw,
        "potencia_max_kw": s.potencia_max_kw,
        "status_final": s.status_final,
        "motivo_encerramento": s.motivo_encerramento,
        "status_cobranca": s.status_cobranca,
        "score_anomalia": s.score_anomalia,
        "motivos_ia": json.loads(s.anomalia_motivos) if s.anomalia_motivos else [],
        "ajuste_valor": s.ajuste_valor,
        "ajuste_motivo": s.ajuste_motivo,
        "fatura_id": s.fatura_id,
    }
    if com_eventos:
        dados["eventos"] = [
            {
                "timestamp": e.timestamp.isoformat(timespec="minutes"),
                "tipo": e.tipo_evento,
                "dados": json.loads(e.payload_json),
            }
            for e in s.eventos
        ]
    return dados


def fatura_dict(f: m.Fatura) -> dict:
    return {
        "id": f.id,
        "unidade_id": f.unidade_id,
        "unidade": f.unidade.rotulo,
        "mes_referencia": f.mes_referencia,
        "qtd_sessoes": f.qtd_sessoes,
        "kwh_total": round(f.kwh_total, 3),
        "valor_energia": f.valor_energia,
        "valor_perdas": f.valor_perdas,
        "valor_infra": f.valor_infra,
        "valor_ociosidade": f.valor_ociosidade,
        "ajustes": f.ajustes,
        "valor_total": f.valor_total,
        "status": f.status,
        "sessoes_pendentes": f.sessoes_pendentes,
        "gerada_em": f.gerada_em.isoformat(timespec="minutes"),
        "memoria_calculo": json.loads(f.memoria_calculo),
    }


def alerta_dict(a: m.AlertaIA) -> dict:
    return {
        "id": a.id,
        "criado_em": a.criado_em.isoformat(timespec="minutes"),
        "tipo": a.tipo,
        "severidade": a.severidade,
        "sessao_id": a.sessao_id,
        "titulo": a.titulo,
        "mensagem": a.mensagem,
        "acao_sugerida": a.acao_sugerida,
        "status": a.status,
        "resolucao": a.resolucao,
    }


def parametros_dict(p: m.ParametrosRateio) -> dict:
    return {
        "tarifa_kwh": p.tarifa_kwh,
        "perdas_percentual": p.perdas_percentual,
        "taxa_infra": p.taxa_infra,
        "cobrar_infra_sem_uso": p.cobrar_infra_sem_uso,
        "ociosidade_ativa": p.ociosidade_ativa,
        "tolerancia_ociosidade_min": p.tolerancia_ociosidade_min,
        "penalidade_ociosidade_hora": p.penalidade_ociosidade_hora,
    }


def notificacao_dict(n: m.Notificacao) -> dict:
    return {
        "id": n.id,
        "criado_em": n.criado_em.isoformat(timespec="minutes"),
        "sessao_id": n.sessao_id,
        "tipo": n.tipo,
        "titulo": n.titulo,
        "mensagem": n.mensagem,
        "canal": n.canal,
        "lida": n.lida,
        "lida_em": n.lida_em.isoformat(timespec="minutes") if n.lida_em else None,
    }

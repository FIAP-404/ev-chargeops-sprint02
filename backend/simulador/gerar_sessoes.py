"""Simulador de sessões de recarga (item 4 do plano da Sprint 01).

Como o SEMS ainda não liberou o histórico de sessões do HCA G2 (ver README),
geramos ~6 meses de uso de um condomínio com um carregador compartilhado.

Ponto importante: o simulador NÃO grava direto no banco. Ele faz o papel do
carregador e dos moradores e chama as mesmas funções que a API usa
(iniciar_sessao/iniciar_sessao_app, registrar_telemetria, encerrar_sessao). Assim os dados
simulados passam pelas mesmas regras, pela IA e pelo rateio que os dados
reais passariam.

O que o simulador reproduz:
- perfis de uso diferentes (noturno, diurno, intensivo, ocasional, híbrido);
- adoção crescente: novos moradores começam a usar ao longo dos meses;
- preferência por terças a quintas à noite (gera o gargalo previsto pela IA);
- disputa pelo carregador: quem chega e encontra ocupado gera uma tentativa
  registrada e às vezes volta mais tarde;
- ociosidade depois da carga completa e sessões interrompidas;
- ~30% das sessões iniciadas pelo app (QR Code do carregador) em vez do cartão;
- ~4% de anomalias injetadas (falha de medição, pico de potência, cabo com
  defeito, conectado sem carregar, falha do carregador, cartão desconhecido).

Uso (dentro da pasta backend):
    python -m simulador.gerar_sessoes            # gera até agora, apagando o banco anterior
    python -m simulador.gerar_sessoes --semanas 20 --seed 7
"""
import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import event, select, update

from app import models as m
from app.database import Base, SessionLocal, engine
from app.faturamento import gerar_faturas, valor_da_sessao
from app.ia import servico as ia
from app.seed import CADASTRO, CARREGADOR_ID, QR_TOKEN_CARREGADOR, popular_cadastro
from app.sessoes import (
    encerrar_sessao,
    hash_rfid,
    iniciar_sessao,
    iniciar_sessao_app,
    registrar_tentativa_ocupado,
    registrar_telemetria,
    revisar_sessao,
)

INTERVALO_TELEMETRIA = timedelta(minutes=15)
POTENCIA_CARREGADOR = 7.4

# peso de cada dia da semana (seg..dom) para quem carrega à noite
PESO_NOTURNO = [1.0, 1.7, 1.3, 1.4, 0.7, 0.5, 0.8]
PESO_UNIFORME = [1.0] * 7


@dataclass
class Perfil:
    rfid: str
    frequencia_semana: float  # sessões por semana
    hora_media: float  # hora de chegada típica
    desvio_hora: float
    kwh_min: float
    kwh_max: float
    semana_inicio: int  # semana em que o morador começa a usar o carregador
    pesos_dia: list[float]
    crescimento: float = 0.0  # aumento da frequência por semana de uso


# frequências e horários pensados para o carregador ficar disputado à noite
PERFIS = [
    Perfil("RFID-A1204-01", 1.8, 19.4, 0.8, 10, 24, 0, PESO_NOTURNO),
    Perfil("RFID-A1204-02", 0.8, 13.5, 2.0, 6, 16, 0, PESO_UNIFORME),
    Perfil("RFID-A0302-01", 2.2, 20.0, 0.7, 22, 40, 0, PESO_NOTURNO, 0.01),
    Perfil("RFID-A0507-01", 1.6, 19.0, 0.9, 10, 22, 4, PESO_NOTURNO),
    Perfil("RFID-A0811-01", 1.8, 9.5, 1.0, 15, 30, 0, PESO_UNIFORME),
    Perfil("RFID-A1502-01", 1.6, 19.6, 0.8, 14, 30, 9, PESO_NOTURNO, 0.01),
    Perfil("RFID-B0104-01", 1.5, 20.4, 0.8, 12, 28, 13, PESO_NOTURNO),
    Perfil("RFID-B0406-01", 3.2, 18.6, 1.0, 5, 14, 0, PESO_NOTURNO),
    Perfil("RFID-B0709-01", 1.2, 22.0, 0.6, 20, 40, 17, PESO_UNIFORME),
    Perfil("RFID-B1101-01", 1.6, 19.2, 0.8, 10, 24, 18, PESO_NOTURNO),
    Perfil("RFID-B1310-01", 0.9, 15.0, 2.0, 8, 20, 22, PESO_UNIFORME),
    Perfil("RFID-B1608-01", 1.6, 20.0, 0.9, 14, 28, 23, PESO_NOTURNO),
]

# dados do veículo de cada cartão, vindos do cadastro base
VEICULO_DO_RFID = {
    rfid: {"bateria": bateria, "potencia_ac": min(ac, POTENCIA_CARREGADOR), "modelo": modelo}
    for _b, _n, _nome, _email, veiculos in CADASTRO
    for (_placa, modelo, bateria, ac, rfid) in veiculos
}

TIPOS_ANOMALIA = [
    "salto_medidor",  # leitura do medidor "pula" -> kWh maior que a bateria
    "pico_potencia",  # telemetria com potência impossível para o carregador
    "cabo_defeituoso",  # potência muito baixa por horas (só o modelo pega)
    "sem_carregar",  # conectado sem transferir energia
    "falha_carregador",  # sessão encerrada por falha
    "cartao_desconhecido",  # visitante com cartão não cadastrado
]


class Simulador:
    def __init__(self, db, rng: random.Random, inicio: datetime, ate: datetime, fator_demanda: float = 0.65,
                 taxa_anomalias: float = 0.07, rng_metodo: random.Random | None = None):
        self.taxa_anomalias = taxa_anomalias  # chance de uma anomalia por dia
        self.db = db
        self.fator_demanda = fator_demanda  # multiplica a frequência de todos os perfis
        self.rng = rng
        # sorteio separado para RFID x app: usar self.rng mudaria todas as sessões
        # geradas depois do primeiro sorteio (e os números citados no README)
        self.rng_metodo = rng_metodo or random.Random(1_000_003)
        self.inicio = inicio
        self.ate = ate
        self.medidor = 1520.0  # leitura acumulada do medidor do carregador (kWh)
        self.livre_em = inicio
        self.stats = {"sessoes": 0, "tentativas_ocupado": 0, "anomalias_injetadas": 0}
        self.gabarito: list[dict] = []  # anomalias injetadas (para avaliar a IA no notebook)
        # Roteiro de demonstração: ocorrências fixas nos últimos dias, para que
        # o mês corrente sempre tenha pendências para o gestor analisar.
        perfil = {p.rfid: p for p in PERFIS}
        base = ate.replace(hour=0, minute=0)
        self.roteiro = {
            (base - timedelta(days=6)).date(): [(10.5, perfil["RFID-A0302-01"], "cartao_desconhecido")],
            (base - timedelta(days=4)).date(): [(13.0, perfil["RFID-A1204-02"], "salto_medidor")],
            (base - timedelta(days=2)).date(): [(9.25, perfil["RFID-A0811-01"], "falha_carregador")],
            (base - timedelta(days=1)).date(): [(14.0, perfil["RFID-A0507-01"], "cabo_defeituoso")],
        }

    # ------------------------------------------------------------------
    def chegadas_do_dia(self, dia: datetime, semana: int) -> list[tuple[datetime, Perfil, str | None]]:
        chegadas = []
        for p in PERFIS:
            if semana < p.semana_inicio:
                continue
            freq = self.fator_demanda * p.frequencia_semana * (1 + p.crescimento * (semana - p.semana_inicio))
            prob = freq * p.pesos_dia[dia.weekday()] / sum(p.pesos_dia)
            if self.rng.random() < prob:
                hora = min(max(self.rng.gauss(p.hora_media, p.desvio_hora), 6.0), 23.3)
                chegadas.append((dia + timedelta(minutes=round(hora * 60)), p, None))
        # anomalias raras
        if self.rng.random() < self.taxa_anomalias:
            tipo = self.rng.choice(TIPOS_ANOMALIA)
            perfil = self.rng.choice([p for p in PERFIS if semana >= p.semana_inicio])
            hora = self.rng.uniform(8, 22)
            chegadas.append((dia + timedelta(minutes=round(hora * 60)), perfil, tipo))
        for hora, perfil, tipo in self.roteiro.get(dia.date(), []):
            chegadas.append((dia + timedelta(minutes=round(hora * 60)), perfil, tipo))
        return sorted(chegadas, key=lambda c: c[0])

    # ------------------------------------------------------------------
    def tentar(self, quando: datetime, perfil: Perfil, anomalia: str | None) -> datetime | None:
        retentativa = anomalia == "retentativa"
        anomalia = None if retentativa else anomalia
        rfid = "VISITANTE-" + str(self.rng.randint(100, 999)) if anomalia == "cartao_desconhecido" else perfil.rfid
        if quando < self.livre_em:
            # O simulador executa cada sessão inteira de uma vez, então no banco
            # não existe sessão "aberta" neste instante. Por isso registramos a
            # tentativa diretamente (na API, iniciar_sessao faz isso sozinho).
            credencial = self.db.scalar(
                select(m.CredencialRFID).where(m.CredencialRFID.codigo_rfid_hash == hash_rfid(rfid))
            )
            registrar_tentativa_ocupado(self.db, CARREGADOR_ID, credencial.usuario_id if credencial else None, quando)
            self.stats["tentativas_ocupado"] += 1
            # metade dos moradores volta depois que o carregador liberar
            if not retentativa and self.rng.random() < 0.5:
                volta = self.livre_em + timedelta(minutes=self.rng.randint(10, 50))
                if volta.date() == quando.date() and volta.hour < 23:
                    return volta
            return None
        self.executar_sessao(quando, perfil, rfid, anomalia)
        return None

    def executar_sessao(self, inicio: datetime, perfil: Perfil, rfid: str, anomalia: str | None) -> None:
        rng = self.rng
        veic = VEICULO_DO_RFID[perfil.rfid]
        kwh_desejado = min(rng.uniform(perfil.kwh_min, perfil.kwh_max), veic["bateria"] * 0.9)
        potencia = veic["potencia_ac"] * rng.uniform(0.93, 0.99)
        if anomalia == "cabo_defeituoso":
            potencia = rng.uniform(1.0, 1.6)
            kwh_desejado = rng.uniform(4, 8)
        if anomalia == "sem_carregar":
            potencia, kwh_desejado = 0.03, 0.06

        if anomalia:
            self.stats["anomalias_injetadas"] += 1
            self.stats.setdefault("por_tipo", {}).setdefault(anomalia, 0)
            self.stats["por_tipo"][anomalia] += 1
        interrompe = rng.random() < 0.12 or anomalia == "falha_carregador"
        fracao = rng.uniform(0.35, 0.85) if interrompe else 1.0
        if anomalia == "sem_carregar":
            fracao = 1.0
        horas_carga = kwh_desejado / potencia * fracao
        if anomalia == "sem_carregar":
            horas_carga = rng.uniform(1.5, 3.0)

        # visitante não tem o app; os moradores usam o app em ~30% das recargas
        usa_app = anomalia != "cartao_desconhecido" and self.rng_metodo.random() < 0.30
        if usa_app:
            cred = self.db.scalar(
                select(m.CredencialRFID).where(m.CredencialRFID.codigo_rfid_hash == hash_rfid(perfil.rfid))
            )
            sessao = iniciar_sessao_app(
                self.db, CARREGADOR_ID, QR_TOKEN_CARREGADOR, cred.usuario_id, cred.veiculo_id, inicio, self.medidor
            )
        else:
            sessao = iniciar_sessao(self.db, CARREGADOR_ID, rfid, inicio, self.medidor)
        if anomalia:
            self.gabarito.append({"sessao_id": sessao.id, "tipo": anomalia, "inicio_em": inicio.isoformat()})
        t, energia, fim_carga = inicio, 0.0, inicio + timedelta(minutes=max(round(horas_carga * 60), 1))
        salto_feito = False
        while t + INTERVALO_TELEMETRIA < fim_carga:
            t += INTERVALO_TELEMETRIA
            restante = (fim_carga - t).total_seconds() / 3600
            # redução de potência nos últimos 20 min (fim da curva de carga)
            p_atual = potencia * (0.6 if restante < 0.33 and not interrompe else 1.0)
            energia += p_atual * INTERVALO_TELEMETRIA.total_seconds() / 3600
            leitura = self.medidor + energia
            if anomalia == "salto_medidor" and not salto_feito and energia > 3:
                self.medidor += rng.uniform(30, 45)  # medidor "pula"
                leitura = self.medidor + energia
                salto_feito = True
            if anomalia == "pico_potencia" and not salto_feito:
                p_atual, salto_feito = rng.uniform(14, 19), True
            registrar_telemetria(self.db, sessao.id, t, round(p_atual, 2), round(leitura, 3), "carregando")

        # última parcela de energia até o fim da carga
        energia += potencia * 0.6 * max((fim_carga - t).total_seconds(), 0) / 3600
        leitura_final = round(self.medidor + energia, 3)

        if anomalia == "falha_carregador":
            registrar_telemetria(self.db, sessao.id, fim_carga, 0.0, leitura_final, "erro")
            encerrar_sessao(self.db, sessao.id, "falha_carregador", fim_carga, leitura_final)
            fim = fim_carga
        elif interrompe:
            encerrar_sessao(self.db, sessao.id, "usuario_desconectou", fim_carga, leitura_final)
            fim = fim_carga
        else:
            registrar_telemetria(self.db, sessao.id, fim_carga, 0.0, leitura_final, "carga_completa")
            sorteio = rng.random()
            if sorteio < 0.62:
                ociosa = rng.uniform(0, 30)  # dentro da tolerância
            elif sorteio < 0.95:
                ociosa = rng.uniform(30, 120)
            else:
                ociosa = rng.uniform(180, 420)  # esqueceu o carro na vaga
            fim = fim_carga + timedelta(minutes=round(ociosa))
            encerrar_sessao(self.db, sessao.id, "usuario_desconectou", fim, leitura_final)

        self.medidor = leitura_final
        self.livre_em = fim
        self.stats["sessoes"] += 1

    # ------------------------------------------------------------------
    def rodar(self) -> None:
        dia = self.inicio
        mes_atual = dia.strftime("%Y-%m")
        while dia < self.ate:
            semana = (dia - self.inicio).days // 7
            if dia.strftime("%Y-%m") != mes_atual:
                # virada de mês: gestor revisa pendências, fecha faturas e a IA é retreinada
                self.fechar_mes(mes_atual, dia)
                mes_atual = dia.strftime("%Y-%m")

            fila = self.chegadas_do_dia(dia, semana)
            while fila:
                quando, perfil, anomalia = fila.pop(0)
                if quando >= self.ate:
                    break
                volta = self.tentar(quando, perfil, anomalia)
                if volta:
                    fila.append((volta, perfil, "retentativa"))
                    fila.sort(key=lambda c: c[0])
            dia += timedelta(days=1)

        # avisos de recargas com mais de 7 dias já foram vistos pelo morador;
        # assim a caixa de avisos mostra como não lido só o que é recente
        antigas = select(m.SessaoRecarga.id).where(m.SessaoRecarga.inicio_em < self.ate - timedelta(days=7))
        self.db.execute(
            update(m.Notificacao).where(m.Notificacao.sessao_id.in_(antigas)).values(lida=True, lida_em=self.ate)
        )
        self.db.commit()

    def fechar_mes(self, mes: str, agora: datetime) -> None:
        revisar_pendencias_do_gestor(self.db, mes, self.rng, agora)
        gerar_faturas(self.db, mes)
        info = ia.treinar_modelos(self.db, agora)
        print(f"  {mes}: faturas geradas | IA retreinada: "
              f"anomalias={info['anomalias'].get('treinado')} demanda={info['demanda'].get('treinado')}")


def revisar_pendencias_do_gestor(db, mes: str, rng: random.Random, agora: datetime) -> None:
    """Simula a síndica tratando os alertas do mês antes de fechar as faturas."""
    from app.faturamento import limites_do_mes

    inicio, fim = limites_do_mes(mes)
    moradores = list(db.scalars(select(m.Usuario).where(m.Usuario.perfil == "morador", m.Usuario.unidade_id.is_not(None))))
    retidas = db.scalars(
        select(m.SessaoRecarga).where(
            m.SessaoRecarga.inicio_em >= inicio,
            m.SessaoRecarga.inicio_em < fim,
            m.SessaoRecarga.status_cobranca.in_((m.COBRANCA_QUARENTENA, m.COBRANCA_REVISAO_IA)),
        )
    ).all()
    for s in retidas:
        motivos = ia.motivos_da_sessao(s)
        codigos = {mo["codigo"] for mo in motivos}
        if s.status_cobranca == m.COBRANCA_QUARENTENA:
            anfitriao = rng.choice(moradores)
            revisar_sessao(db, s.id, "liberar", usuario_id=anfitriao.id,
                           observacao=f"visitante de {anfitriao.nome}", agora=agora)
        elif codigos & {"kwh_acima_da_bateria", "conectado_sem_carregar"}:
            revisar_sessao(db, s.id, "descartar", observacao="erro de medição confirmado", agora=agora)
        elif "falha_equipamento" in codigos:
            # R$ 5 de crédito, limitado ao valor da sessão (uma falha logo no início vale menos que isso)
            credito = min(5.0, valor_da_sessao(db, s))
            revisar_sessao(db, s.id, "liberar", ajuste_valor=credito,
                           observacao=f"falha confirmada, crédito de R$ {credito:.2f}".replace(".", ","), agora=agora)
        else:
            revisar_sessao(db, s.id, "liberar", observacao="leitura conferida", agora=agora)

    # avisos de ociosidade dos meses fechados são dados como tratados
    for alerta in db.scalars(
        select(m.AlertaIA).where(
            m.AlertaIA.status == "aberto", m.AlertaIA.severidade == "baixa",
            m.AlertaIA.criado_em < fim,
        )
    ):
        alerta.status, alerta.resolucao = "resolvido", "usuário avisado"
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera sessões simuladas do EV ChargeOps")
    parser.add_argument("--semanas", type=int, default=27, help="semanas de histórico (padrão: 27)")
    parser.add_argument("--ate", type=str, default=None, help="data/hora final ISO (padrão: agora)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fator-demanda", type=float, default=0.65,
                        help="escala a frequência de uso de todos os moradores (padrão: 0.65)")
    parser.add_argument("--taxa-anomalias", type=float, default=0.07,
                        help="probabilidade de uma anomalia injetada por dia (padrão: 0.07)")
    parser.add_argument("--sem-roteiro", action="store_true",
                        help="não injeta o roteiro fixo de demonstração nos últimos dias")
    args = parser.parse_args()

    ate = datetime.fromisoformat(args.ate) if args.ate else datetime.now().replace(second=0, microsecond=0)
    inicio = (ate - timedelta(weeks=args.semanas)).replace(hour=0, minute=0)
    inicio -= timedelta(days=inicio.weekday())  # começa numa segunda-feira

    # recria o banco do zero
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ia.resetar_modelos()

    @event.listens_for(engine, "connect")
    def _sqlite_rapido(conn, _):  # acelera a simulação (muitos commits pequenos)
        conn.execute("PRAGMA synchronous=OFF")

    engine.dispose()
    db = SessionLocal()
    popular_cadastro(db)

    print(f"Simulando de {inicio:%d/%m/%Y} até {ate:%d/%m/%Y %H:%M} (seed={args.seed})...")
    sim = Simulador(db, random.Random(args.seed), inicio, ate, args.fator_demanda, args.taxa_anomalias,
                    rng_metodo=random.Random(args.seed + 1_000_003))
    if args.sem_roteiro:
        sim.roteiro = {}
    sim.rodar()

    # fim da simulação: treina com tudo e gera os alertas de capacidade
    info = ia.treinar_modelos(db, ate)
    ia.atualizar_alertas_capacidade(db, ate)
    # gabarito das anomalias injetadas: usado no notebook para medir a IA
    import csv

    from app.database import PASTA_DADOS

    with open(PASTA_DADOS / "anomalias_injetadas.csv", "w", newline="", encoding="utf-8") as arq:
        escritor = csv.DictWriter(arq, fieldnames=["sessao_id", "tipo", "inicio_em"])
        escritor.writeheader()
        escritor.writerows(sim.gabarito)
    print(f"Concluído: {sim.stats}")
    print(f"Modelo de anomalias: {info['anomalias']}")
    print(f"Modelo de demanda: {info['demanda']}")
    db.close()


if __name__ == "__main__":
    main()

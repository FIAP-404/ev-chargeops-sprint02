"""Tabelas do banco (SQLAlchemy).

Segue o esquema definido na seção 5.6 do README da Sprint 01. Os campos novos
(ex.: status_cobranca, score_anomalia, minutos_ociosos) foram adicionados
durante a implementação e estão justificados no README da Sprint 02.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# --- Status usados no ciclo de vida da sessão --------------------------------
# status_final: o que aconteceu fisicamente com a recarga
STATUS_EM_ANDAMENTO = "em_andamento"
STATUS_CONCLUIDA = "concluida"
STATUS_INTERROMPIDA = "interrompida"  # usuário/carregador encerrou antes da carga completa
STATUS_FALHA = "falha"  # erro do equipamento ou de comunicação

# status_cobranca: se a sessão pode entrar na fatura
COBRANCA_AGUARDANDO = "aguardando"  # sessão ainda aberta
COBRANCA_LIBERADA = "liberada"  # pronta para o rateio
COBRANCA_QUARENTENA = "quarentena"  # sem identificação do usuário (regra da Sprint 01)
COBRANCA_REVISAO_IA = "revisao_ia"  # IA marcou como anômala, gestor precisa decidir
COBRANCA_FATURADA = "faturada"  # já entrou em uma fatura fechada
COBRANCA_DESCARTADA = "descartada"  # gestor decidiu não cobrar


class Unidade(Base):
    __tablename__ = "unidades"

    id: Mapped[int] = mapped_column(primary_key=True)
    bloco: Mapped[str] = mapped_column(String(10))
    numero: Mapped[str] = mapped_column(String(10))
    responsavel_usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="unidade")

    @property
    def rotulo(self) -> str:
        return f"{self.bloco}-{self.numero}"


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120))
    perfil: Mapped[str] = mapped_column(String(20), default="morador")  # morador | gestor | visitante
    status: Mapped[str] = mapped_column(String(20), default="ativo")  # ativo | inativo
    unidade_id: Mapped[int | None] = mapped_column(ForeignKey("unidades.id"), nullable=True)

    unidade: Mapped[Unidade | None] = relationship(back_populates="usuarios")
    veiculos: Mapped[list["Veiculo"]] = relationship(back_populates="usuario")


class Veiculo(Base):
    __tablename__ = "veiculos"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    placa: Mapped[str] = mapped_column(String(10))
    modelo: Mapped[str] = mapped_column(String(60))
    capacidade_bateria_kwh: Mapped[float] = mapped_column(Float)
    # limite do carregador de bordo (AC) do veículo; a IA usa para saber a potência esperada
    potencia_ac_max_kw: Mapped[float] = mapped_column(Float, default=7.4)

    usuario: Mapped[Usuario] = relationship(back_populates="veiculos")


class Carregador(Base):
    __tablename__ = "carregadores"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # ex.: goodwe_hca_g2_l1
    fabricante: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(40))
    numero_serie: Mapped[str] = mapped_column(String(60))
    localizacao: Mapped[str] = mapped_column(String(120))
    potencia_max_kw: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="disponivel")


class CredencialRFID(Base):
    __tablename__ = "credenciais_rfid"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    veiculo_id: Mapped[int | None] = mapped_column(ForeignKey("veiculos.id"), nullable=True)
    # guardamos só o hash do cartão, nunca o código em texto puro
    codigo_rfid_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="ativo")


class SessaoRecarga(Base):
    __tablename__ = "sessoes_recarga"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    unidade_id: Mapped[int | None] = mapped_column(ForeignKey("unidades.id"), nullable=True)
    veiculo_id: Mapped[int | None] = mapped_column(ForeignKey("veiculos.id"), nullable=True)
    carregador_id: Mapped[str] = mapped_column(ForeignKey("carregadores.id"))
    rfid_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    inicio_em: Mapped[datetime] = mapped_column(DateTime, index=True)
    fim_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    carga_completa_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    leitura_inicial_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    leitura_final_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    kwh_entregue: Mapped[float] = mapped_column(Float, default=0.0)
    duracao_min: Mapped[float] = mapped_column(Float, default=0.0)
    minutos_ociosos: Mapped[float] = mapped_column(Float, default=0.0)
    potencia_media_kw: Mapped[float] = mapped_column(Float, default=0.0)
    potencia_max_kw: Mapped[float] = mapped_column(Float, default=0.0)

    status_final: Mapped[str] = mapped_column(String(20), default=STATUS_EM_ANDAMENTO)
    motivo_encerramento: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status_cobranca: Mapped[str] = mapped_column(String(20), default=COBRANCA_AGUARDANDO, index=True)

    # preenchidos pelo módulo de IA no encerramento
    score_anomalia: Mapped[float | None] = mapped_column(Float, nullable=True)
    anomalia_motivos: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON (lista)

    # crédito concedido pelo gestor (falha comprovada, contestação aceita)
    ajuste_valor: Mapped[float] = mapped_column(Float, default=0.0)
    ajuste_motivo: Mapped[str | None] = mapped_column(String(200), nullable=True)

    fatura_id: Mapped[int | None] = mapped_column(ForeignKey("faturas.id"), nullable=True)

    eventos: Mapped[list["EventoSessao"]] = relationship(
        back_populates="sessao", cascade="all, delete-orphan", order_by="EventoSessao.timestamp"
    )
    usuario: Mapped[Usuario | None] = relationship()
    unidade: Mapped[Unidade | None] = relationship()
    veiculo: Mapped[Veiculo | None] = relationship()


class EventoSessao(Base):
    __tablename__ = "eventos_sessao"

    id: Mapped[int] = mapped_column(primary_key=True)
    sessao_id: Mapped[int] = mapped_column(ForeignKey("sessoes_recarga.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    tipo_evento: Mapped[str] = mapped_column(String(30))  # inicio | telemetria | carga_completa | erro | fim | revisao
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    sessao: Mapped[SessaoRecarga] = relationship(back_populates="eventos")


class TentativaOcupado(Base):
    """Usuário passou o RFID mas o carregador já estava em uso.

    Não estava no esquema da Sprint 01. Foi adicionada porque, com um único
    carregador, as sessões registradas escondem a demanda reprimida: quem chega
    e encontra a vaga ocupada não gera sessão. A previsão de demanda usa
    sessões + tentativas para medir a procura real.
    """

    __tablename__ = "tentativas_ocupado"

    id: Mapped[int] = mapped_column(primary_key=True)
    carregador_id: Mapped[str] = mapped_column(ForeignKey("carregadores.id"))
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)


class ParametrosRateio(Base):
    """Regras de rateio aprovadas pelo gestor/condomínio (linha única, id=1)."""

    __tablename__ = "parametros_rateio"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    tarifa_kwh: Mapped[float] = mapped_column(Float, default=0.95)
    perdas_percentual: Mapped[float] = mapped_column(Float, default=5.0)
    taxa_infra: Mapped[float] = mapped_column(Float, default=20.0)
    # Sprint 01: "taxa fixa só se a convenção/regra interna aprovar cobrança de disponibilidade"
    cobrar_infra_sem_uso: Mapped[bool] = mapped_column(Boolean, default=False)
    # Sprint 01: ociosidade é opcional e depende de regra interna
    ociosidade_ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    tolerancia_ociosidade_min: Mapped[float] = mapped_column(Float, default=30.0)
    penalidade_ociosidade_hora: Mapped[float] = mapped_column(Float, default=6.0)


class Fatura(Base):
    __tablename__ = "faturas"
    __table_args__ = (UniqueConstraint("unidade_id", "mes_referencia"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unidade_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"))
    mes_referencia: Mapped[str] = mapped_column(String(7))  # AAAA-MM
    qtd_sessoes: Mapped[int] = mapped_column(Integer, default=0)
    kwh_total: Mapped[float] = mapped_column(Float, default=0.0)
    valor_energia: Mapped[float] = mapped_column(Float, default=0.0)
    valor_perdas: Mapped[float] = mapped_column(Float, default=0.0)
    valor_infra: Mapped[float] = mapped_column(Float, default=0.0)
    valor_ociosidade: Mapped[float] = mapped_column(Float, default=0.0)
    ajustes: Mapped[float] = mapped_column(Float, default=0.0)
    valor_total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="fechada")
    sessoes_pendentes: Mapped[int] = mapped_column(Integer, default=0)
    gerada_em: Mapped[datetime] = mapped_column(DateTime)
    memoria_calculo: Mapped[str] = mapped_column(Text, default="{}")  # JSON com cada parcela

    unidade: Mapped[Unidade] = relationship()


class AlertaIA(Base):
    __tablename__ = "alertas_ia"

    id: Mapped[int] = mapped_column(primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime)
    tipo: Mapped[str] = mapped_column(String(30))  # anomalia_sessao | sessao_sem_identificacao | capacidade
    severidade: Mapped[str] = mapped_column(String(10))  # baixa | media | alta
    sessao_id: Mapped[int | None] = mapped_column(ForeignKey("sessoes_recarga.id"), nullable=True)
    titulo: Mapped[str] = mapped_column(String(160))
    mensagem: Mapped[str] = mapped_column(Text)
    acao_sugerida: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="aberto")  # aberto | resolvido
    resolucao: Mapped[str | None] = mapped_column(String(200), nullable=True)

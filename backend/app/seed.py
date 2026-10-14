"""Cadastro base do cenário simulado.

Todos os nomes, placas e cartões são fictícios. O cenário representa um
condomínio com um carregador GoodWe HCA G2 compartilhado no estacionamento L1
(mesmo modelo do Energy Innovation Lab da FIAP). A unidade A-1204 é a do
exemplo da Sprint 01 e tem dois veículos, para demonstrar a consolidação.
"""
from sqlalchemy.orm import Session

from app import models as m
from app.sessoes import hash_rfid

CARREGADOR_ID = "goodwe_hca_g2_l1"
# texto do QR Code colado no carregador (lido pelo app do morador)
QR_TOKEN_CARREGADOR = "QR-HCA01-7F3A"

# potencia_ac_kw = limite do carregador de bordo do veículo (o HCA G2 entrega até 7,4 kW em monofásico)
CADASTRO = [
    # bloco, número, nome, e-mail, [(placa, modelo, bateria_kwh, potencia_ac_kw, rfid)]
    ("A", "1204", "Marina Albuquerque", "marina.a1204@exemplo.com",
     [("RTA1C24", "BYD Dolphin", 44.9, 6.6, "RFID-A1204-01"),
      ("SPB2D35", "Renault Kwid E-Tech", 26.8, 6.6, "RFID-A1204-02")]),
    ("A", "302", "Rafael Tanaka", "rafael.a302@exemplo.com",
     [("FTK3E46", "BYD Seal", 82.5, 7.0, "RFID-A0302-01")]),
    ("A", "507", "Juliana Ferraz", "juliana.a507@exemplo.com",
     [("GJF4F57", "BYD Dolphin Mini", 38.0, 6.6, "RFID-A0507-01")]),
    ("A", "811", "Paulo Henrique Lima", "paulo.a811@exemplo.com",
     [("HPL5G68", "Volvo EX30", 69.0, 7.4, "RFID-A0811-01")]),
    ("A", "1502", "Beatriz Nogueira", "beatriz.a1502@exemplo.com",
     [("JBN6H79", "GWM Ora 03", 63.0, 6.6, "RFID-A1502-01")]),
    ("B", "104", "Carlos Eduardo Ramos", "carlos.b104@exemplo.com",
     [("KCR7J81", "Peugeot e-2008", 50.0, 7.4, "RFID-B0104-01")]),
    ("B", "406", "Fernanda Okada", "fernanda.b406@exemplo.com",
     [("LFO8K92", "BYD Song Plus DM-i", 18.3, 3.3, "RFID-B0406-01")]),
    ("B", "709", "Gustavo Mendes", "gustavo.b709@exemplo.com",
     [("MGM9L13", "Chevrolet Bolt EUV", 65.0, 7.2, "RFID-B0709-01")]),
    ("B", "1101", "Helena Costa", "helena.b1101@exemplo.com",
     [("NHC1M24", "BYD Dolphin", 44.9, 6.6, "RFID-B1101-01")]),
    ("B", "1310", "Igor Santana", "igor.b1310@exemplo.com",
     [("PIS2N35", "BYD Dolphin Mini", 38.0, 6.6, "RFID-B1310-01")]),
    ("B", "1608", "Larissa Prado", "larissa.b1608@exemplo.com",
     [("QLP3P46", "Volvo EX30", 51.0, 7.4, "RFID-B1608-01")]),
    # unidade sem veículo elétrico: deve ter fatura zerada
    ("B", "202", "Otávio Siqueira", "otavio.b202@exemplo.com", []),
]

GESTOR = ("Síndica Regina Alves", "gestao@exemplo.com")


def popular_cadastro(db: Session) -> None:
    if db.get(m.Carregador, CARREGADOR_ID):
        return  # já populado
    db.add(
        m.Carregador(
            id=CARREGADOR_ID,
            fabricante="GoodWe",
            modelo="HCA G2 7kW",
            numero_serie="HCA-G2-SIM-0001",
            localizacao="Estacionamento L1",
            potencia_max_kw=7.4,
            status="disponivel",
            qr_token=QR_TOKEN_CARREGADOR,
        )
    )
    db.add(m.ParametrosRateio(id=1))
    db.add(m.Usuario(nome=GESTOR[0], email=GESTOR[1], perfil="gestor"))

    for bloco, numero, nome, email, veiculos in CADASTRO:
        unidade = m.Unidade(bloco=bloco, numero=numero)
        db.add(unidade)
        db.flush()
        usuario = m.Usuario(nome=nome, email=email, perfil="morador", unidade_id=unidade.id)
        db.add(usuario)
        db.flush()
        unidade.responsavel_usuario_id = usuario.id
        for placa, modelo, bateria, potencia_ac, rfid in veiculos:
            veiculo = m.Veiculo(
                usuario_id=usuario.id,
                placa=placa,
                modelo=modelo,
                capacidade_bateria_kwh=bateria,
                potencia_ac_max_kw=potencia_ac,
            )
            db.add(veiculo)
            db.flush()
            db.add(m.CredencialRFID(usuario_id=usuario.id, veiculo_id=veiculo.id, codigo_rfid_hash=hash_rfid(rfid)))
    db.commit()

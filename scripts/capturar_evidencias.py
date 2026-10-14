"""Gera as capturas de tela de evidência (docs/evidencias/01 a 06 .png), a fatura em
PDF (07-fatura.pdf), a caixa de avisos do morador (08-avisos-morador.png), o
consumo por carregador do painel do gestor (09-consumo-por-carregador.png) e o
CSV do mês exportado pela tela de faturas (faturas-AAAA-MM.csv).

Pré-requisitos: API e front-end rodando sobre o banco do simulador
(python -m simulador.gerar_sessoes --ate 2026-10-13T15:00). O front-end é lido
em http://localhost:5173; para outro endereço, defina EVCO_FRONT_URL.
    pip install playwright && python -m playwright install chromium
    python scripts/capturar_evidencias.py
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("EVCO_FRONT_URL", "http://localhost:5173").rstrip("/")
PASTA = Path(__file__).resolve().parent.parent / "docs" / "evidencias"
PASTA.mkdir(parents=True, exist_ok=True)

PAGINAS = [
    ("01-painel-gestor", "#/painel", "Decisões pendentes"),
    ("02-faturas", "#/faturas", "Total rateado"),
    ("03-area-morador", "#/morador", "Recargas do mês"),
    ("05-regras-rateio", "#/regras", "Salvar regras"),
]


def main(largura: int = 1440) -> None:
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pagina = nav.new_page(viewport={"width": largura, "height": 900}, device_scale_factor=1)

        for nome, rota, texto in PAGINAS:
            pagina.goto(f"{URL}/{rota}")
            pagina.get_by_text(texto).first.wait_for()
            pagina.wait_for_timeout(1200)  # gráficos terminam de desenhar
            pagina.screenshot(path=PASTA / f"{nome}.png", full_page=True)
            print("ok", nome)

        # consumo por carregador no painel do gestor
        pagina.goto(f"{URL}/#/painel")
        carregadores = pagina.locator("section[aria-labelledby='t-carregadores']")
        carregadores.locator("tbody tr").first.wait_for()
        pagina.wait_for_timeout(500)
        carregadores.screenshot(path=PASTA / "09-consumo-por-carregador.png")
        print("ok 09-consumo-por-carregador")

        # caixa de avisos do morador (resumo das recargas e ociosidade)
        pagina.goto(f"{URL}/#/morador")
        avisos = pagina.locator("section[aria-labelledby='t-avisos']")
        avisos.locator("li").first.wait_for()
        pagina.wait_for_timeout(500)
        avisos.screenshot(path=PASTA / "08-avisos-morador.png")
        print("ok 08-avisos-morador")

        # fatura impressa em PDF (regras de @media print) e CSV do mês para a administradora
        pagina.goto(f"{URL}/#/faturas")
        pagina.get_by_text("Total rateado").first.wait_for()
        pagina.pdf(path=PASTA / "07-fatura.pdf", format="A4", print_background=True)
        print("ok 07-fatura")
        with pagina.expect_download() as download:
            pagina.get_by_role("link", name="Exportar CSV").click()
        download.value.save_as(PASTA / download.value.suggested_filename)
        print("ok", download.value.suggested_filename)

        # simulação ao vivo: recarga com cabo defeituoso, avaliada pela IA
        pagina.goto(f"{URL}/#/simular")
        pagina.get_by_label("Situação").select_option("cabo")
        pagina.get_by_role("combobox", name="Cartão RFID").select_option("RFID-A0811-01")
        pagina.get_by_role("button", name="Iniciar recarga").click()
        pagina.get_by_text("Sessão encerrada e avaliada pela IA").wait_for(timeout=60000)
        pagina.wait_for_timeout(500)
        pagina.screenshot(path=PASTA / "04-simular-recarga.png", full_page=True)
        print("ok 04-simular-recarga")

        # versão celular do painel do morador
        celular = nav.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        celular.goto(f"{URL}/#/morador")
        celular.get_by_text("Recargas do mês").first.wait_for()
        celular.wait_for_timeout(800)
        celular.screenshot(path=PASTA / "06-area-morador-celular.png", full_page=True)
        print("ok 06-area-morador-celular")
        nav.close()


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))

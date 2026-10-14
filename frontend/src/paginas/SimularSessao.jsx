import { useEffect, useRef, useState } from "react";
import { api, useApi } from "../api";
import { StatusCobranca, textoStatusFinal } from "../componentes/Status";
import { duracao, kwh, num } from "../formatos";

// Cartões do cenário simulado (no banco só existe o hash de cada um)
const CARTOES = [
  ["RFID-A1204-01", "A-1204, Marina (BYD Dolphin, 44,9 kWh)", 6.6],
  ["RFID-A1204-02", "A-1204, Marina (Renault Kwid E-Tech, 26,8 kWh)", 6.6],
  ["RFID-A0302-01", "A-302, Rafael (BYD Seal, 82,5 kWh)", 7.0],
  ["RFID-A0811-01", "A-811, Paulo (Volvo EX30, 69 kWh)", 7.4],
  ["RFID-B0406-01", "B-406, Fernanda (BYD Song Plus DM-i, 18,3 kWh)", 3.3],
  ["RFID-B1608-01", "B-1608, Larissa (Volvo EX30, 51 kWh)", 7.4],
  ["CARTAO-VISITANTE", "Cartão não cadastrado (visitante)", 7.0],
];

// Texto do QR Code colado no carregador (o mesmo de app/seed.py)
const QR_CARREGADOR = "QR-HCA01-7F3A";

const CENARIOS = {
  normal: { nome: "Recarga normal", kwh: 14, ociosoMin: 12 },
  ociosa: { nome: "Carro esquecido na vaga após a carga (4 h)", kwh: 12, ociosoMin: 240 },
  medidor: { nome: "Erro de medição: medidor salta para 90 kWh", kwh: 10, salto: 80, ociosoMin: 10 },
  cabo: { nome: "Cabo com defeito: potência de 1,3 kW", kwh: 5.2, potencia: 1.3, ociosoMin: 5 },
  falha: { nome: "Falha do carregador no meio da recarga", kwh: 16, falha: 0.45, ociosoMin: 0 },
};

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));
const isoLocal = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);

/**
 * Faz o papel do carregador: chama a API passo a passo (iniciar, telemetria,
 * encerrar), exatamente como um gateway conectado ao HCA G2 faria. No modo
 * app, o início vem do app do morador que leu o QR Code do carregador.
 */
export default function SimularSessao() {
  const [modo, setModo] = useState("rfid");
  const [cartao, setCartao] = useState(CARTOES[0][0]);
  const usuarios = useApi("/usuarios");
  const [usuarioId, setUsuarioId] = useState(null);
  const [veiculoId, setVeiculoId] = useState(null);
  const [cenario, setCenario] = useState("normal");
  const [registro, setRegistro] = useState([]);
  const [resultado, setResultado] = useState(null);
  const [rodando, setRodando] = useState(false);

  // no app só entra morador com unidade e veículo; sem escolha, fica o primeiro
  const moradores = (usuarios.dados ?? []).filter((u) => u.unidade && u.veiculos_detalhe.length > 0);
  const morador = moradores.find((u) => u.id === usuarioId) ?? moradores[0];
  const veiculo = morador?.veiculos_detalhe.find((v) => v.id === veiculoId) ?? morador?.veiculos_detalhe[0];

  const fimDoRegistro = useRef(null);
  const log = (t, texto) => setRegistro((r) => [...r, { t, texto }]);

  // mantém a última leitura visível enquanto a recarga acontece
  useEffect(() => {
    const lista = fimDoRegistro.current;
    if (lista) lista.scrollTop = lista.scrollHeight;
  }, [registro]);

  async function executar() {
    setRodando(true);
    setRegistro([]);
    setResultado(null);
    const c = CENARIOS[cenario];
    const potenciaVeiculo = modo === "app" ? Math.min(veiculo.potencia_ac_max_kw, 7.4) : CARTOES.find((x) => x[0] === cartao)[2];
    const potencia = c.potencia ?? potenciaVeiculo * 0.95;
    const horasCarga = (c.kwh / potencia) * (c.falha ?? 1);
    const duracaoMs = (horasCarga * 60 + c.ociosoMin) * 60000;
    const hora = (d) => d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

    try {
      // a sessão termina "agora": o início é calculado para trás, sem invadir a
      // recarga anterior (a API recusa sessões sobrepostas no mesmo carregador).
      // fim_em vem sem os segundos, por isso o minuto a mais. Cliques seguidos
      // encadeiam as recargas, então a simulada pode terminar no futuro.
      const [ultima] = await api("/sessoes?limite=1");
      const livreEm = ultima?.fim_em ? new Date(ultima.fim_em).getTime() + 60000 : 0;
      const inicio = new Date(Math.max(Date.now() - duracaoMs, livreEm));
      const fimCarga = new Date(inicio.getTime() + horasCarga * 3600000);
      const fim = new Date(fimCarga.getTime() + c.ociosoMin * 60000);

      // sem leitura_inicial_kwh, a API continua o medidor de onde a última sessão parou
      const sessao = modo === "app"
        ? await api("/sessoes/iniciar-app", {
          metodo: "POST",
          corpo: { qr_token: QR_CARREGADOR, usuario_id: morador.id, veiculo_id: veiculo.id, inicio_em: isoLocal(inicio) },
        })
        : await api("/sessoes/iniciar", {
          metodo: "POST",
          corpo: { codigo_rfid: cartao, inicio_em: isoLocal(inicio) },
        });
      const leituraInicial = sessao.leitura_inicial_kwh;
      if (modo === "app") {
        log(hora(inicio), `QR Code lido pelo app. Sessão #${sessao.id} aberta para a unidade ${sessao.unidade}. Medidor em ${num(leituraInicial, 0)} kWh.`);
      } else {
        log(hora(inicio), `RFID lido. Sessão #${sessao.id} aberta ${sessao.unidade ? `para a unidade ${sessao.unidade}` : "sem morador identificado"}. Medidor em ${num(leituraInicial, 0)} kWh.`);
      }

      let t = new Date(inicio);
      let energia = 0;
      let leitura = leituraInicial;
      while (t.getTime() + 15 * 60000 < fimCarga.getTime()) {
        t = new Date(t.getTime() + 15 * 60000);
        energia += potencia * 0.25;
        if (c.salto && energia > 3 && leitura - leituraInicial < c.salto) energia += c.salto;
        leitura = leituraInicial + energia;
        await api(`/sessoes/${sessao.id}/telemetria`, {
          metodo: "POST",
          corpo: { timestamp: isoLocal(t), potencia_kw: Number(potencia.toFixed(2)), energia_acumulada_kwh: Number(leitura.toFixed(3)) },
        });
        log(hora(t), `Telemetria: ${num(potencia)} kW, ${kwh(energia)} acumulados.`);
        await esperar(70);
      }
      const leituraFinal = Number((leituraInicial + Math.max(energia, c.kwh * (c.falha ?? 1))).toFixed(3));

      let motivo = "usuario_desconectou";
      if (c.falha) {
        await api(`/sessoes/${sessao.id}/telemetria`, {
          metodo: "POST",
          corpo: { timestamp: isoLocal(fimCarga), potencia_kw: 0, energia_acumulada_kwh: leituraFinal, status_carregador: "erro" },
        });
        log(hora(fimCarga), "Carregador reportou erro.");
        motivo = "falha_carregador";
      } else {
        await api(`/sessoes/${sessao.id}/telemetria`, {
          metodo: "POST",
          corpo: { timestamp: isoLocal(fimCarga), potencia_kw: 0, energia_acumulada_kwh: leituraFinal, status_carregador: "carga_completa" },
        });
        log(hora(fimCarga), "Carga completa. A partir daqui o tempo conta como ociosidade.");
      }

      const final = await api(`/sessoes/${sessao.id}/encerrar`, {
        metodo: "POST",
        corpo: { motivo, fim_em: isoLocal(fim), leitura_final_kwh: leituraFinal },
      });
      log(hora(fim), "Cabo desconectado. Sessão encerrada e avaliada pela IA.");
      setResultado(final);
    } catch (e) {
      log("—", e.status === 409 ? "Carregador ocupado: a tentativa foi registrada como procura não atendida." : `Erro: ${e.message}`);
    } finally {
      setRodando(false);
    }
  }

  return (
    <>
      <header className="cabecalho">
        <div>
          <h1>Simular uma recarga</h1>
          <p>
            Esta tela faz o papel do carregador: identifica o morador pelo cartão ou pelo QR Code lido no app, envia a
            telemetria e encerra a sessão pela API. O resultado passa pelas mesmas regras, pela IA e pelo rateio de uma
            recarga real.
          </p>
        </div>
      </header>

      <div className="grade grade-2">
        <section className="bloco" aria-labelledby="t-config">
          <header><h2 id="t-config">Recarga</h2></header>
          <div className="pilha" style={{ gap: 14 }}>
            <fieldset className="opcoes">
              <legend>Identificação</legend>
              <label>
                <input type="radio" name="modo" checked={modo === "rfid"} onChange={() => setModo("rfid")} />
                Cartão RFID
              </label>
              <label>
                <input type="radio" name="modo" checked={modo === "app"} onChange={() => setModo("app")} />
                App (QR Code)
              </label>
            </fieldset>
            {modo === "rfid" ? (
              <label className="campo">
                Cartão RFID
                <select value={cartao} onChange={(e) => setCartao(e.target.value)}>
                  {CARTOES.map(([cod, nome]) => <option key={cod} value={cod}>{nome}</option>)}
                </select>
              </label>
            ) : (
              <>
                <div className="qr-adesivo">
                  <code>{QR_CARREGADOR}</code>
                  <span>Adesivo do carregador, Estacionamento L1</span>
                </div>
                <label className="campo">
                  Morador (app)
                  <select
                    value={morador?.id ?? ""}
                    onChange={(e) => { setUsuarioId(Number(e.target.value)); setVeiculoId(null); }}
                  >
                    {moradores.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.unidade}, {u.nome} ({u.veiculos_detalhe.map((v) => v.modelo).join(" e ")})
                      </option>
                    ))}
                  </select>
                </label>
                {morador?.veiculos_detalhe.length > 1 && (
                  <label className="campo">
                    Veículo
                    <select value={veiculo.id} onChange={(e) => setVeiculoId(Number(e.target.value))}>
                      {morador.veiculos_detalhe.map((v) => <option key={v.id} value={v.id}>{v.modelo} ({v.placa})</option>)}
                    </select>
                  </label>
                )}
              </>
            )}
            <label className="campo">
              Situação
              <select value={cenario} onChange={(e) => setCenario(e.target.value)}>
                {Object.entries(CENARIOS).map(([k, c]) => <option key={k} value={k}>{c.nome}</option>)}
              </select>
            </label>
            <div>
              <button
                className="botao"
                onClick={executar}
                disabled={rodando || (modo === "app" && !veiculo)}
                aria-busy={rodando || undefined}
              >
                {rodando ? "Recarregando…" : modo === "app" ? "Ler QR Code e iniciar recarga" : "Iniciar recarga"}
              </button>
            </div>
          </div>

          {resultado && <Resultado s={resultado} />}
        </section>

        <section className="bloco" aria-labelledby="t-registro">
          <header>
            <h2 id="t-registro">O que a API recebeu</h2>
            <p>uma leitura a cada 15 min</p>
          </header>
          {registro.length === 0 ? (
            <p className="vazio">Escolha a identificação e a situação e inicie a recarga.</p>
          ) : (
            <ol className="registro" aria-live="polite" ref={fimDoRegistro}>
              {registro.map((r, i) => (
                <li key={i}><span className="t">{r.t}</span><span>{r.texto}</span></li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </>
  );
}

function Resultado({ s }) {
  const retida = s.status_cobranca === "revisao_ia" || s.status_cobranca === "quarentena";
  return (
    <div style={{ marginTop: 22, borderTop: "1px solid var(--linha)", paddingTop: 18 }} aria-live="polite">
      <div className="linha-controles" style={{ alignItems: "center", marginBottom: 8 }}>
        <h3>Sessão #{s.id}</h3>
        <StatusCobranca status={s.status_cobranca} />
      </div>
      <p>
        {kwh(s.kwh_entregue, 2)} em {duracao(s.duracao_min)} ({textoStatusFinal(s.status_final).toLowerCase()}),
        potência média de {num(s.potencia_media_kw)} kW
        {s.minutos_ociosos > 0 ? `, ${duracao(s.minutos_ociosos)} ocupando a vaga depois da carga` : ""}.
      </p>
      {s.motivos_ia.length > 0 ? (
        <ul className="motivos">
          {s.motivos_ia.map((m) => <li key={m.codigo} className={m.severidade}>{m.descricao}</li>)}
        </ul>
      ) : (
        <p className="mudo" style={{ marginTop: 6 }}>A IA não encontrou nada fora do padrão.</p>
      )}
      <p className="acao-sugerida" style={{ marginTop: 10 }}>
        {retida
          ? "A cobrança ficou retida e aparece em Decisões pendentes no painel do gestor."
          : "Liberada para o rateio: entra na fatura do mês com as regras vigentes."}
      </p>
    </div>
  );
}

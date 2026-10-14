import { useState } from "react";
import { api, useApi } from "../api";
import DecisoesPendentes from "../componentes/DecisoesPendentes";
import { Barras, BarrasHorizontais } from "../componentes/Graficos";
import { FaixasCriticas, MapaDeCalor } from "../componentes/PrevisaoCapacidade";
import { Severidade } from "../componentes/Status";
import { brl, dataCurta, dataHora, kwh, mesAtual, nomeMes, num } from "../formatos";

export default function PainelGestor() {
  const [mes, setMes] = useState(mesAtual());
  const resumo = useApi(`/dashboard/resumo?mes=${mes}`);
  const historico = useApi("/dashboard/historico-mensal");
  const previsao = useApi("/ia/previsao-capacidade");
  const alertas = useApi("/ia/alertas?status=aberto");
  const usuarios = useApi("/usuarios");
  const statusIa = useApi("/ia/status");

  const recarregar = () => {
    resumo.recarregar();
    alertas.recarregar();
    historico.recarregar();
  };

  if (resumo.erro) return <ErroApi erro={resumo.erro} />;
  const r = resumo.dados;
  const meses = historico.dados?.map((h) => h.mes) ?? [mes];
  const moradores = (usuarios.dados ?? []).filter((u) => u.unidade);
  const avisos = (alertas.dados ?? []).filter((a) => a.tipo === "anomalia_sessao" && a.severidade === "baixa");
  const modelo = statusIa.dados?.anomalias;

  return (
    <>
      <header className="cabecalho">
        <div>
          <h1>Painel do gestor</h1>
          <p>Consumo, rateio e decisões do carregador compartilhado em {nomeMes(mes)}.</p>
        </div>
        <label className="campo">
          Mês
          <select className="seletor" value={mes} onChange={(e) => setMes(e.target.value)}>
            {[...meses].reverse().map((m) => <option key={m} value={m}>{nomeMes(m)}</option>)}
          </select>
        </label>
      </header>

      {r && (
        <section className="indicadores" aria-label="Indicadores do mês">
          <Indicador valor={kwh(r.kpis.kwh, 0)} rotulo="Energia entregue" nota={`${r.kpis.sessoes} sessões`} />
          <Indicador valor={brl(r.kpis.valor_rateado)} rotulo="Valor rateado" nota={`${r.kpis.unidades_ativas} unidades com uso`} />
          <Indicador valor={`${num(r.kpis.ocupacao_pct, 0)}%`} rotulo="Ocupação do carregador" nota="tempo conectado no mês" />
          <Indicador valor={r.kpis.tentativas_ocupado} rotulo="Procura não atendida" nota="chegadas com carregador ocupado" />
          <Indicador valor={duracaoHoras(r.kpis.minutos_ociosos)} rotulo="Vaga ocupada sem carregar" nota="após carga completa" />
          <Indicador
            valor={r.kpis.sessoes_retidas}
            rotulo="Decisões pendentes"
            nota="sessões fora da fatura"
            destaque={r.kpis.sessoes_retidas > 0}
          />
        </section>
      )}

      <div className="grade grade-3-2">
        <section className="bloco" aria-labelledby="t-decisoes">
          <header>
            <h2 id="t-decisoes">Decisões pendentes</h2>
            <p>A IA retém a cobrança de sessões fora do padrão até você decidir.</p>
            {modelo?.cold_start && (
              <p className="mudo" style={{ flexBasis: "100%" }}>
                Modelo desligado (cold start): só as regras de negócio avaliam as sessões. O Isolation Forest liga com{" "}
                {modelo.minimo_sessoes} sessões no histórico (hoje: {modelo.sessoes_elegiveis}).
              </p>
            )}
          </header>
          {r ? (
            <DecisoesPendentes
              sessoes={r.sessoes_retidas}
              moradores={moradores}
              alertas={alertas.dados ?? []}
              aoDecidir={recarregar}
            />
          ) : <p className="vazio">Carregando…</p>}
        </section>

        <div className="pilha">
          <section className="bloco" aria-labelledby="t-capacidade">
            <header>
              <h2 id="t-capacidade">Capacidade prevista</h2>
              <p>Previsão da IA para as próximas semanas</p>
            </header>
            {previsao.dados?.disponivel ? (
              <>
                <FaixasCriticas faixas={previsao.dados.faixas} horizonte={previsao.dados.horizonte_semanas} />
                <p className="mudo" style={{ marginTop: 14 }}>
                  Barra violeta: próxima semana. Traço preto: daqui a {previsao.dados.horizonte_semanas} semanas. Linha
                  tracejada: limite de 1 carregador. A procura cresce cerca de{" "}
                  {num(previsao.dados.metricas_modelo.crescimento_semanal_pct)}% por semana.
                </p>
              </>
            ) : (
              <p className="vazio">{previsao.dados?.motivo ?? "Carregando…"}</p>
            )}
          </section>

          <section className="bloco" aria-labelledby="t-avisos">
            <header>
              <h2 id="t-avisos">Avisos aos moradores</h2>
              <p>Não retêm cobrança</p>
            </header>
            {avisos.length ? (
              avisos.map((a) => <Aviso key={a.id} alerta={a} aoResolver={alertas.recarregar} />)
            ) : (
              <p className="vazio">Nenhum aviso em aberto.</p>
            )}
          </section>
        </div>
      </div>

      {previsao.dados?.disponivel && (
        <section className="bloco" style={{ marginTop: 20 }} aria-labelledby="t-calor">
          <header>
            <h2 id="t-calor">Quando a procura vai acontecer na próxima semana</h2>
            <p>
              Regressão de Poisson treinada com {previsao.dados.metricas_modelo.semanas_historico} semanas. No teste com
              as últimas 4 semanas, errou {num(previsao.dados.metricas_modelo.erro_semanal_medio_pct_modelo, 0)}% no
              total semanal, contra {num(previsao.dados.metricas_modelo.erro_semanal_medio_pct_baseline, 0)}% da média
              histórica simples.
            </p>
          </header>
          <MapaDeCalor grade={previsao.dados.grade_proxima_semana} />
        </section>
      )}

      {r && (
        <div className="grade grade-2 topo" style={{ marginTop: 20 }}>
          <section className="bloco" aria-labelledby="t-unidades">
            <header>
              <h2 id="t-unidades">Consumo por unidade</h2>
              <p>kWh liberados para cobrança</p>
            </header>
            <BarrasHorizontais
              dados={r.consumo_por_unidade.filter((u) => u.kwh > 0)}
              categoria="unidade"
              valor="kwh"
              formatarValor={(v) => num(v, 0)}
              dica={(u) => [`Unidade ${u.unidade}`, kwh(u.kwh), `${u.sessoes} sessões`, brl(u.valor_total)]}
            />
          </section>

          <div className="pilha">
            <section className="bloco" aria-labelledby="t-diario">
              <header>
                <h2 id="t-diario">Energia por dia</h2>
                <p>kWh</p>
              </header>
              <Barras
                dados={r.consumo_diario}
                x="data"
                y="kwh"
                altura={180}
                formatarX={dataCurta}
                dica={(d) => [dataCurta(d.data), kwh(d.kwh), `${d.sessoes} sessões`]}
              />
            </section>
            {historico.dados && (
              <section className="bloco" aria-labelledby="t-historico">
                <header>
                  <h2 id="t-historico">Energia por mês</h2>
                  <p>kWh</p>
                </header>
                <Barras
                  dados={historico.dados}
                  x="mes"
                  y="kwh"
                  altura={160}
                  formatarX={(m) => nomeMes(m, true)}
                  dica={(h) => [nomeMes(h.mes), kwh(h.kwh, 0), `${h.sessoes} sessões`, `${h.tentativas_ocupado} chegadas com carregador ocupado`]}
                />
              </section>
            )}
          </div>
        </div>
      )}

      {r && (
        <section className="bloco" style={{ marginTop: 20 }} aria-labelledby="t-carregadores">
          <header>
            <h2 id="t-carregadores">Consumo por carregador</h2>
            <p>Valor das sessões sem a taxa de infraestrutura, que é cobrada por unidade</p>
          </header>
          <div className="tabela-rolagem">
            <table>
              <thead>
                <tr>
                  <th>Carregador</th>
                  <th>Local</th>
                  <th className="num">Sessões</th>
                  <th className="num">Energia</th>
                  <th className="num">Ocupação</th>
                  <th className="num">Sem carregar</th>
                  <th className="num">Procura não atendida</th>
                  <th className="num">Valor das sessões</th>
                </tr>
              </thead>
              <tbody>
                {r.consumo_por_carregador.map((c) => (
                  <tr key={c.carregador_id}>
                    <td>{c.fabricante} {c.modelo}</td>
                    <td>{c.localizacao}</td>
                    <td className="num">{c.sessoes}</td>
                    <td className="num">{kwh(c.kwh, 0)}</td>
                    <td className="num">{num(c.ocupacao_pct, 0)}%</td>
                    <td className="num">{duracaoHoras(c.minutos_ociosos)}</td>
                    <td className="num">{c.tentativas_ocupado}</td>
                    <td className="num">{brl(c.valor_sessoes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mudo" style={{ marginTop: 8 }}>
            Valores incluem sessões ainda em revisão; a fatura cobra só as liberadas.
          </p>
        </section>
      )}
    </>
  );
}

function Indicador({ valor, rotulo, nota, destaque }) {
  return (
    <div className={`indicador ${destaque ? "destaque" : ""}`}>
      <div className="rotulo">{rotulo}</div>
      <div className="valor">{valor}</div>
      <div className="nota">{nota}</div>
    </div>
  );
}

function Aviso({ alerta, aoResolver }) {
  async function resolver() {
    await api(`/ia/alertas/${alerta.id}/resolver`, { metodo: "POST", corpo: { resolucao: "morador avisado" } });
    aoResolver();
  }
  return (
    <div className="alerta">
      <Severidade nivel={alerta.severidade} />
      <div>
        <div>{alerta.titulo}</div>
        <div className="msg">{alerta.mensagem} {dataHora(alerta.criado_em)}.</div>
      </div>
      <button className="botao secundario" onClick={resolver}>Marcar como avisado</button>
    </div>
  );
}

function duracaoHoras(min) {
  return `${num(min / 60, 0)} h`;
}

export function ErroApi({ erro }) {
  return (
    <div className="bloco">
      <h2>Não foi possível falar com a API</h2>
      <p className="mudo" style={{ marginTop: 8 }}>
        {erro.message}. Confira se o back-end está rodando em http://localhost:8000 (uvicorn app.main:app) e recarregue
        a página.
      </p>
    </div>
  );
}

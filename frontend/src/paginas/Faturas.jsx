import { useEffect, useState } from "react";
import { api, useApi } from "../api";
import FaturaExplicavel from "../componentes/FaturaExplicavel";
import { brl, kwh, mesAtual, nomeMes, nomeMesTitulo } from "../formatos";
import { ErroApi } from "./PainelGestor";

export default function Faturas() {
  const historico = useApi("/dashboard/historico-mensal");
  const [mes, setMes] = useState(null);
  const [faturas, setFaturas] = useState(null);
  const [selecionada, setSelecionada] = useState(null);
  const [gerando, setGerando] = useState(false);
  const [mensagem, setMensagem] = useState(null);

  // começa no último mês já fechado
  useEffect(() => {
    if (historico.dados && !mes) {
      const fechados = historico.dados.filter((h) => h.fechado);
      setMes(fechados.length ? fechados[fechados.length - 1].mes : mesAtual());
    }
  }, [historico.dados, mes]);

  useEffect(() => {
    if (!mes) return;
    api(`/faturas?mes=${mes}`).then((lista) => {
      setFaturas(lista);
      setSelecionada(lista.find((f) => f.unidade === "A-1204") ?? lista[0] ?? null);
    });
  }, [mes]);

  async function fecharMes() {
    setGerando(true);
    setMensagem(null);
    try {
      const lista = await api(`/faturas/gerar?mes=${mes}`, { metodo: "POST" });
      setFaturas(lista);
      setSelecionada(lista.find((f) => f.qtd_sessoes > 0) ?? lista[0]);
      const pend = lista.reduce((s, f) => s + f.sessoes_pendentes, 0);
      setMensagem(
        `${lista.length} faturas geradas para ${nomeMes(mes)}.` +
          (pend ? ` ${pend} sessões em revisão ficaram de fora; gere de novo depois de decidir.` : "")
      );
      historico.recarregar();
    } finally {
      setGerando(false);
    }
  }

  if (historico.erro) return <ErroApi erro={historico.erro} />;
  const meses = historico.dados?.map((h) => h.mes).reverse() ?? [];
  const total = faturas?.reduce((s, f) => s + f.valor_total, 0) ?? 0;

  return (
    <>
      <header className="cabecalho">
        <div>
          <h1>Faturas por unidade</h1>
          <p>Cada fatura mostra a regra por trás de cada valor. Unidades que não carregaram no mês não pagam consumo.</p>
        </div>
        <div className="linha-controles">
          <label className="campo">
            Mês
            <select className="seletor" value={mes ?? ""} onChange={(e) => { setMes(e.target.value); setMensagem(null); }}>
              {meses.map((m) => <option key={m} value={m}>{nomeMes(m)}</option>)}
            </select>
          </label>
          <button className="botao" onClick={fecharMes} disabled={gerando || !mes}>
            {faturas?.length ? "Recalcular faturas do mês" : "Fechar mês e gerar faturas"}
          </button>
        </div>
      </header>

      {mensagem && <p className="aviso neutro" style={{ marginBottom: 16 }}>{mensagem}</p>}

      <div className="grade grade-3-2">
        <section className="bloco" aria-labelledby="t-lista">
          <header>
            <h2 id="t-lista">{mes ? nomeMesTitulo(mes) : ""}</h2>
            {faturas?.length > 0 && <p>Total rateado: {brl(total)}</p>}
          </header>
          {!faturas ? (
            <p className="vazio">Carregando…</p>
          ) : faturas.length === 0 ? (
            <p className="vazio">
              Este mês ainda não foi fechado. Use "Fechar mês e gerar faturas" para calcular o rateio de todas as unidades.
            </p>
          ) : (
            <div className="tabela-rolagem">
              <table>
                <thead>
                  <tr>
                    <th>Unidade</th>
                    <th className="num">Sessões</th>
                    <th className="num">Energia</th>
                    <th className="num">Ociosidade</th>
                    <th className="num">Créditos</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {faturas.map((f) => (
                    <tr
                      key={f.id}
                      className={`clicavel ${selecionada?.id === f.id ? "selecionada" : ""}`}
                      onClick={() => setSelecionada(f)}
                      tabIndex={0}
                      onKeyDown={(e) => e.key === "Enter" && setSelecionada(f)}
                    >
                      <td>{f.unidade}{f.sessoes_pendentes > 0 && <span className="mudo"> ({f.sessoes_pendentes} em revisão)</span>}</td>
                      <td className="num">{f.qtd_sessoes}</td>
                      <td className="num">{kwh(f.kwh_total)}</td>
                      <td className="num">{brl(f.valor_ociosidade)}</td>
                      <td className="num">{f.ajustes ? `− ${brl(f.ajustes)}` : "—"}</td>
                      <td className="num"><strong>{brl(f.valor_total)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div>
          {selecionada && (
            <FaturaExplicavel
              unidade={selecionada.unidade}
              mes={selecionada.mes_referencia}
              linhas={selecionada.memoria_calculo.linhas}
              total={selecionada.valor_total}
              kwhTotal={selecionada.kwh_total}
              qtdSessoes={selecionada.qtd_sessoes}
              porVeiculo={selecionada.memoria_calculo.kwh_por_veiculo}
              pendentes={selecionada.sessoes_pendentes}
            />
          )}
        </div>
      </div>
    </>
  );
}

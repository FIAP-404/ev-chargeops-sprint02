import { brl, kwh, nomeMes } from "../formatos";

/**
 * A fatura explicável: cada parcela aparece com a regra que gerou o valor.
 * Recebe a memória de cálculo do motor de rateio (back-end, app/rateio.py).
 */
export default function FaturaExplicavel({ unidade, mes, linhas, total, kwhTotal, qtdSessoes, porVeiculo, pendentes = 0, previa = false }) {
  const veiculos = Object.entries(porVeiculo || {});
  return (
    <article className="fatura" aria-label={`Fatura da unidade ${unidade}`}>
      <div className="topo">
        <div>
          <h2>Unidade {unidade}</h2>
          <p className="mes">{previa ? "Prévia de " : "Fatura de "}{nomeMes(mes)}</p>
        </div>
        <p className="mudo">EV ChargeOps</p>
      </div>

      <div className="leituras">
        <div><span className="r">Energia medida</span><span className="v">{kwh(kwhTotal, 2)}</span></div>
        <div><span className="r">Sessões</span><span className="v">{qtdSessoes}</span></div>
        {veiculos.map(([placa, valor]) => (
          <div key={placa}><span className="r">Veículo {placa}</span><span className="v">{kwh(valor, 1)}</span></div>
        ))}
      </div>

      <ol>
        {linhas.map((l) => (
          <li key={l.parcela}>
            <div className="item">
              <span className="nome">{l.parcela}</span>
              <span className="pontos" />
              <span className={`valor ${l.valor < 0 ? "credito" : ""}`}>{l.valor < 0 ? `− ${brl(-l.valor)}` : brl(l.valor)}</span>
            </div>
            <div className="regra">{l.regra}</div>
          </li>
        ))}
      </ol>

      <div className="total">
        <span>{previa ? "Total até agora" : "Total a pagar"}</span>
        <strong>{brl(total)}</strong>
      </div>

      <div className="rodape">
        {pendentes > 0 && (
          <div className="aviso" role="note">
            {pendentes === 1 ? "1 sessão ainda não entrou" : `${pendentes} sessões ainda não entraram`} neste valor (em
            andamento ou em revisão pelo gestor).
          </div>
        )}
        <span>Cálculo: energia × tarifa + perdas técnicas + taxa de infraestrutura + ociosidade − créditos.</span>
      </div>
    </article>
  );
}

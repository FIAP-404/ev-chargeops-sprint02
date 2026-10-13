import { useState } from "react";
import { api } from "../api";
import { dataHora, duracao, kwh, num } from "../formatos";
import { StatusCobranca } from "./Status";

const ORIGEM = { regra: "Regra de negócio", modelo: "Modelo (Isolation Forest)" };

/**
 * Sessões que a IA reteve ou que estão sem identificação. Nada aqui entra na
 * fatura até o gestor decidir: é o ponto onde a IA participa do fluxo de cobrança.
 */
export default function DecisoesPendentes({ sessoes, moradores, alertas, aoDecidir }) {
  if (!sessoes.length) {
    return <p className="vazio">Nenhuma sessão aguardando decisão. Todas as recargas do mês já foram liberadas.</p>;
  }
  return (
    <div>
      {sessoes.map((s) => (
        <Decisao
          key={s.id}
          sessao={s}
          moradores={moradores}
          acao={alertas.find((a) => a.sessao_id === s.id)?.acao_sugerida}
          aoDecidir={aoDecidir}
        />
      ))}
    </div>
  );
}

function Decisao({ sessao: s, moradores, acao, aoDecidir }) {
  const [credito, setCredito] = useState("");
  const [morador, setMorador] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState(null);
  const semDono = s.status_cobranca === "quarentena";

  async function decidir(decisao) {
    setEnviando(true);
    setErro(null);
    try {
      await api(`/sessoes/${s.id}/revisao`, {
        metodo: "POST",
        corpo: {
          decisao,
          usuario_id: semDono && morador ? Number(morador) : null,
          ajuste_valor: Number(credito.replace(",", ".")) || 0,
          observacao: decisao === "liberar" ? (credito ? "crédito concedido pelo gestor" : "conferida pelo gestor") : "não cobrada",
        },
      });
      aoDecidir();
    } catch (e) {
      setErro(e.message);
      setEnviando(false);
    }
  }

  return (
    <section className="decisao" aria-label={`Sessão ${s.id}`}>
      <div>
        <div className="linha-controles" style={{ alignItems: "center" }}>
          <span className="quem">
            Sessão #{s.id} {s.unidade ? `da unidade ${s.unidade}` : "sem morador identificado"}
          </span>
          <StatusCobranca status={s.status_cobranca} />
        </div>
        <p className="detalhe">
          {s.usuario ? `${s.usuario}, ${s.veiculo}. ` : ""}
          {dataHora(s.inicio_em)} a {dataHora(s.fim_em)}, {duracao(s.duracao_min)} conectado, {kwh(s.kwh_entregue)} entregues,
          potência média de {num(s.potencia_media_kw)} kW.
        </p>
        {s.motivos_ia.length > 0 && (
          <ul className="motivos">
            {s.motivos_ia.map((m) => (
              <li key={m.codigo} className={m.severidade}>
                {m.descricao} <span className="origem">({ORIGEM[m.origem]})</span>
              </li>
            ))}
          </ul>
        )}
        {s.score_anomalia != null && (
          <p className="mudo" style={{ marginTop: 6 }}>
            Mais atípica que {Math.round(s.score_anomalia * 100)}% das sessões do histórico.
          </p>
        )}
        {acao && <p className="acao-sugerida">Sugestão da IA: {acao}</p>}
        {erro && <p className="erro">{erro}</p>}
      </div>

      <div className="acoes">
        {semDono && (
          <label className="campo">
            Atribuir a
            <select value={morador} onChange={(e) => setMorador(e.target.value)}>
              <option value="">Escolha o morador</option>
              {moradores.map((m) => (
                <option key={m.id} value={m.id}>{m.unidade}, {m.nome}</option>
              ))}
            </select>
          </label>
        )}
        <button className="botao" disabled={enviando || (semDono && !morador)} onClick={() => decidir("liberar")}>
          Liberar para cobrança
        </button>
        {!semDono && (
          <div className="credito">
            <input
              aria-label="Valor do crédito em reais"
              placeholder="R$ 0,00"
              inputMode="decimal"
              value={credito}
              onChange={(e) => setCredito(e.target.value)}
            />
            <button className="botao secundario" disabled={enviando || !credito} onClick={() => decidir("liberar")}>
              Liberar com crédito
            </button>
          </div>
        )}
        <button className="botao perigo" disabled={enviando} onClick={() => decidir("descartar")}>
          Não cobrar
        </button>
      </div>
    </section>
  );
}

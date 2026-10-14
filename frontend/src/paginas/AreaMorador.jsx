import { useEffect, useState } from "react";
import { api, useApi } from "../api";
import FaturaExplicavel from "../componentes/FaturaExplicavel";
import { StatusCobranca, textoStatusFinal } from "../componentes/Status";
import { brl, dataHora, duracao, kwh, nomeMes, nomeMesTitulo } from "../formatos";
import { ErroApi } from "./PainelGestor";

// avisos mostrados de início; os demais ficam atrás de "Ver todos"
const AVISOS_RECENTES = 5;

/**
 * Área do morador. No protótipo não há login: o seletor simula qual morador
 * está acessando (ver "Desvios" no README).
 */
export default function AreaMorador() {
  const usuarios = useApi("/usuarios");
  const [usuarioId, setUsuarioId] = useState(null);
  const [faturaAberta, setFaturaAberta] = useState(null);
  const [erroAviso, setErroAviso] = useState(null);
  const [todosAvisos, setTodosAvisos] = useState(false);

  useEffect(() => {
    if (usuarios.dados && !usuarioId) {
      const marina = usuarios.dados.find((u) => u.unidade === "A-1204");
      setUsuarioId(marina?.id ?? usuarios.dados.find((u) => u.unidade)?.id);
    }
  }, [usuarios.dados, usuarioId]);

  const painel = useApi(usuarioId ? `/usuarios/${usuarioId}/painel` : null);
  const avisos = useApi(usuarioId ? `/usuarios/${usuarioId}/notificacoes` : null);
  useEffect(() => {
    setFaturaAberta(null);
    setErroAviso(null);
    setTodosAvisos(false);
  }, [usuarioId]);

  async function marcarComoLida(aviso) {
    setErroAviso(null);
    try {
      await api(`/notificacoes/${aviso.id}/lida`, { metodo: "POST" });
      avisos.recarregar();
      painel.recarregar();
    } catch (err) {
      setErroAviso(err.message);
    }
  }

  if (usuarios.erro) return <ErroApi erro={usuarios.erro} />;
  const moradores = (usuarios.dados ?? []).filter((u) => u.unidade);
  const p = painel.dados;

  return (
    <>
      <header className="cabecalho">
        <div>
          <h1>{p ? `Olá, ${p.usuario.nome.split(" ")[0]}` : "Área do morador"}</h1>
          <p>
            {p
              ? `Recargas e fatura da unidade ${p.unidade.rotulo} em ${nomeMes(p.mes)}.`
              : "Recargas e fatura da sua unidade."}
          </p>
        </div>
        <label className="campo">
          Ver como
          <select className="seletor" value={usuarioId ?? ""} onChange={(e) => setUsuarioId(Number(e.target.value))}>
            {moradores.map((u) => <option key={u.id} value={u.id}>{u.unidade}, {u.nome}</option>)}
          </select>
        </label>
      </header>

      {!p ? (
        <p className="vazio">Carregando…</p>
      ) : (
        <div className="grade grade-3-2">
          <div className="pilha">
            <section className="bloco" aria-labelledby="t-avisos">
              <header>
                <h2 id="t-avisos">Avisos</h2>
                {p.notificacoes_nao_lidas > 0 && (
                  <span className="selo atencao">
                    {p.notificacoes_nao_lidas} {p.notificacoes_nao_lidas === 1 ? "não lido" : "não lidos"}
                  </span>
                )}
              </header>
              {avisos.erro ? (
                <ErroApi erro={avisos.erro} />
              ) : !avisos.dados ? (
                <p className="vazio">Carregando…</p>
              ) : avisos.dados.length === 0 ? (
                <p className="vazio">Nenhum aviso.</p>
              ) : (
                <ul className="avisos-lista">
                  {(todosAvisos ? avisos.dados : avisos.dados.slice(0, AVISOS_RECENTES)).map((a) => (
                    <li key={a.id} className={a.lida ? "" : "nao-lida"}>
                      <div>
                        <strong>{a.titulo}</strong> <span className="mudo">{dataHora(a.criado_em)}</span>
                      </div>
                      <p className="msg">{a.mensagem}</p>
                      {!a.lida && (
                        <button className="botao secundario" onClick={() => marcarComoLida(a)}>
                          Marcar como lida
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {avisos.dados?.length > AVISOS_RECENTES && (
                <button className="botao secundario" aria-expanded={todosAvisos} onClick={() => setTodosAvisos(!todosAvisos)}>
                  {todosAvisos ? "Ver menos" : `Ver todos (${avisos.dados.length})`}
                </button>
              )}
              {erroAviso && <p className="erro">{erroAviso}</p>}
            </section>

            {p.projecao_ia && (
              <section className="projecao" aria-labelledby="t-proj">
                <h2 id="t-proj" style={{ marginBottom: 6 }}>Previsão para o fechamento de {nomeMes(p.mes).split(" ")[0]}</h2>
                <div className="numero">{brl(p.projecao_ia.valor_projetado_mes)}</div>
                <p>
                  Se o uso da unidade seguir o padrão das últimas 8 semanas, o mês deve fechar com cerca de{" "}
                  {kwh(p.projecao_ia.kwh_projetado_mes, 0)}. Faltam {p.projecao_ia.dias_restantes} dias. Estimativa da IA,
                  o valor cobrado é sempre o medido.
                </p>
              </section>
            )}

            <section className="bloco" aria-labelledby="t-sessoes">
              <header>
                <h2 id="t-sessoes">Recargas do mês</h2>
                <p>{p.veiculos.map((v) => `${v.modelo} (${v.placa})`).join(" e ")}</p>
              </header>
              {p.sessoes.length === 0 ? (
                <p className="vazio">Nenhuma recarga neste mês. Sem uso, não há cobrança de consumo.</p>
              ) : (
                <div className="tabela-rolagem">
                  <table>
                    <thead>
                      <tr>
                        <th>Início</th>
                        <th>Veículo</th>
                        <th className="num">Energia</th>
                        <th className="num">Duração</th>
                        <th>Situação</th>
                      </tr>
                    </thead>
                    <tbody>
                      {p.sessoes.map((s) => (
                        <tr key={s.id}>
                          <td>{dataHora(s.inicio_em)}</td>
                          <td>
                            {s.veiculo?.split(" (")[0] ?? "—"}
                            <div className="mudo">{s.metodo_identificacao === "app_qr" ? "via app" : "via RFID"}</div>
                          </td>
                          <td className="num">{kwh(s.kwh_entregue)}</td>
                          <td className="num">
                            {duracao(s.duracao_min)}
                            {s.minutos_ociosos > 30 && <div className="mudo">{duracao(s.minutos_ociosos)} após carga</div>}
                          </td>
                          <td>
                            <StatusCobranca status={s.status_cobranca} />
                            {s.status_final !== "concluida" && <div className="mudo">{textoStatusFinal(s.status_final)}</div>}
                            {s.status_cobranca === "revisao_ia" && (
                              <div className="mudo">{s.motivos_ia[0]?.descricao}</div>
                            )}
                            {s.ajuste_valor > 0 && <div className="mudo">Crédito de {brl(s.ajuste_valor)}</div>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="bloco" aria-labelledby="t-historico">
              <header>
                <h2 id="t-historico">Faturas anteriores</h2>
              </header>
              {p.faturas.length === 0 ? (
                <p className="vazio">Nenhuma fatura fechada ainda.</p>
              ) : (
                <table>
                  <tbody>
                    {p.faturas.map((f) => (
                      <tr
                        key={f.id}
                        className={`clicavel ${faturaAberta?.id === f.id ? "selecionada" : ""}`}
                        tabIndex={0}
                        onClick={() => setFaturaAberta(f)}
                        onKeyDown={(e) => e.key === "Enter" && setFaturaAberta(f)}
                      >
                        <td>{nomeMesTitulo(f.mes_referencia)}</td>
                        <td className="num">{kwh(f.kwh_total)}</td>
                        <td className="num"><strong>{brl(f.valor_total)}</strong></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>

          <div className="pilha">
            {faturaAberta ? (
              <>
                <FaturaExplicavel
                  unidade={p.unidade.rotulo}
                  mes={faturaAberta.mes_referencia}
                  linhas={faturaAberta.memoria_calculo.linhas}
                  total={faturaAberta.valor_total}
                  kwhTotal={faturaAberta.kwh_total}
                  qtdSessoes={faturaAberta.qtd_sessoes}
                  porVeiculo={faturaAberta.memoria_calculo.kwh_por_veiculo}
                  pendentes={faturaAberta.sessoes_pendentes}
                />
                <button className="botao secundario" onClick={() => setFaturaAberta(null)}>
                  Voltar para o mês atual
                </button>
              </>
            ) : (
              <FaturaExplicavel
                previa
                unidade={p.unidade.rotulo}
                mes={p.mes}
                linhas={p.fatura_parcial.memoria}
                total={p.fatura_parcial.valor_total}
                kwhTotal={p.fatura_parcial.kwh_total}
                qtdSessoes={p.fatura_parcial.qtd_sessoes}
                porVeiculo={p.fatura_parcial.kwh_por_veiculo}
                pendentes={p.fatura_parcial.sessoes_pendentes.length}
              />
            )}
            <button className="botao secundario" onClick={() => window.print()}>
              Imprimir / salvar PDF
            </button>
          </div>
        </div>
      )}
    </>
  );
}

import { useEffect, useState } from "react";
import { api, useApi } from "../api";
import { brl } from "../formatos";
import { ErroApi } from "./PainelGestor";

const CAMPOS = [
  ["tarifa_kwh", "Tarifa efetiva (R$/kWh)", "Inclui impostos, conforme a conta da distribuidora."],
  ["perdas_percentual", "Perdas técnicas (%)", "Percentual sobre o valor da energia."],
  ["taxa_infra", "Taxa de infraestrutura (R$/mês)", "Manutenção, software e depreciação do carregador."],
  ["tolerancia_ociosidade_min", "Tolerância de ociosidade (min)", "Tempo livre na vaga depois da carga completa."],
  ["penalidade_ociosidade_hora", "Taxa de ociosidade (R$/h)", "Cobrada só sobre o tempo acima da tolerância."],
];

export default function Regras() {
  const atual = useApi("/parametros");
  const [form, setForm] = useState(null);
  const [salvo, setSalvo] = useState(null);
  const [erro, setErro] = useState(null);

  useEffect(() => { if (atual.dados) setForm(atual.dados); }, [atual.dados]);

  if (atual.erro) return <ErroApi erro={atual.erro} />;
  if (!form) return <p className="vazio">Carregando…</p>;

  async function salvar(e) {
    e.preventDefault();
    setErro(null);
    try {
      const corpo = Object.fromEntries(
        Object.entries(form).map(([k, v]) => [k, typeof v === "boolean" ? v : Number(String(v).replace(",", "."))])
      );
      await api("/parametros", { metodo: "PUT", corpo });
      setSalvo(new Date());
    } catch (err) {
      setErro(err.message);
    }
  }

  const exemplo = 42 * Number(form.tarifa_kwh);
  return (
    <>
      <header className="cabecalho">
        <div>
          <h1>Regras de rateio</h1>
          <p>
            Valores aprovados pelo condomínio. Mudanças valem para as próximas faturas; para aplicar num mês já fechado,
            recalcule as faturas dele.
          </p>
        </div>
      </header>

      <div className="grade grade-2">
        <form className="bloco pilha" style={{ gap: 14 }} onSubmit={salvar}>
          {CAMPOS.map(([chave, rotulo, ajuda]) => (
            <label key={chave} className="campo">
              {rotulo}
              <input
                inputMode="decimal"
                value={form[chave]}
                onChange={(e) => setForm({ ...form, [chave]: e.target.value })}
                aria-describedby={`ajuda-${chave}`}
              />
              <span id={`ajuda-${chave}`} className="mudo">{ajuda}</span>
            </label>
          ))}
          <label className="linha-controles" style={{ alignItems: "center" }}>
            <input type="checkbox" checked={form.ociosidade_ativa} onChange={(e) => setForm({ ...form, ociosidade_ativa: e.target.checked })} />
            Cobrar taxa de ociosidade
          </label>
          <label className="linha-controles" style={{ alignItems: "center" }}>
            <input type="checkbox" checked={form.cobrar_infra_sem_uso} onChange={(e) => setForm({ ...form, cobrar_infra_sem_uso: e.target.checked })} />
            Cobrar taxa de infraestrutura de quem não carregou no mês
          </label>
          <div className="linha-controles">
            <button className="botao" type="submit">Salvar regras</button>
            {salvo && <span className="mudo">Regras salvas às {salvo.toLocaleTimeString("pt-BR")}.</span>}
            {erro && <span className="erro">{erro}</span>}
          </div>
        </form>

        <section className="bloco" aria-labelledby="t-exemplo">
          <header><h2 id="t-exemplo">Como fica uma unidade que consumiu 42 kWh</h2></header>
          <table>
            <tbody>
              <tr><td>Energia: 42 kWh × {brl(Number(form.tarifa_kwh))}</td><td className="num">{brl(exemplo)}</td></tr>
              <tr><td>Perdas técnicas: {form.perdas_percentual}%</td><td className="num">{brl(exemplo * Number(form.perdas_percentual) / 100)}</td></tr>
              <tr><td>Infraestrutura</td><td className="num">{brl(Number(form.taxa_infra))}</td></tr>
              <tr>
                <td><strong>Total sem ociosidade</strong></td>
                <td className="num"><strong>{brl(exemplo * (1 + Number(form.perdas_percentual) / 100) + Number(form.taxa_infra))}</strong></td>
              </tr>
            </tbody>
          </table>
          <p className="mudo" style={{ marginTop: 12 }}>
            Exemplo da Sprint 01: com R$ 0,95/kWh, 5% de perdas e R$ 20,00 de taxa, o total é R$ 61,90. O cálculo oficial é
            feito no servidor, com arredondamento de cada parcela em centavos.
          </p>
        </section>
      </div>
    </>
  );
}

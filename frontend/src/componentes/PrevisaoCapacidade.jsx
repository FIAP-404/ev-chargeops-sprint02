import { useState } from "react";
import { num, pct } from "../formatos";
import { StatusCapacidade } from "./Status";

const DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
// rampa sequencial de um só tom (azul), do claro ao escuro
const RAMPA = ["#eef3f8", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

function corDaCelula(valor, maximo) {
  if (valor < 0.02) return RAMPA[0];
  const i = Math.min(RAMPA.length - 1, 1 + Math.floor((valor / maximo) * (RAMPA.length - 1)));
  return RAMPA[i];
}

/** Mapa de calor das chegadas previstas para a próxima semana (modelo de Poisson). */
export function MapaDeCalor({ grade }) {
  const [foco, setFoco] = useState(null);
  const maximo = Math.max(...grade.flat(), 0.1);
  return (
    <div>
      <div className="calor" role="table" aria-label="Chegadas previstas por dia e hora na próxima semana">
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="hora">{h % 3 === 0 ? h : ""}</span>
        ))}
        {grade.map((linha, d) => (
          <div key={d} style={{ display: "contents" }} role="row">
            <span className="dia" role="rowheader">{DIAS[d]}</span>
            {linha.map((v, h) => (
              <span
                key={h}
                role="cell"
                tabIndex={0}
                className="celula"
                style={{ background: corDaCelula(v, maximo), outline: foco?.d === d && foco?.h === h ? "2px solid #16202b" : undefined }}
                aria-label={`${DIAS[d]} ${h}h: ${num(v, 2)} chegadas previstas`}
                onMouseEnter={() => setFoco({ d, h, v })}
                onFocus={() => setFoco({ d, h, v })}
                onMouseLeave={() => setFoco(null)}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="legenda-calor">
        <span>menos</span>
        <span className="escala">{RAMPA.map((c) => <span key={c} style={{ background: c }} />)}</span>
        <span>mais chegadas por hora</span>
        <span style={{ marginLeft: "auto", color: "var(--tinta)" }} aria-live="polite">
          {foco ? `${DIAS[foco.d]}, ${foco.h}h às ${foco.h + 1}h: ${num(foco.v, 2)} chegadas previstas` : "Passe o mouse sobre uma hora"}
        </span>
      </div>
    </div>
  );
}

/**
 * Ocupação por faixa do dia: barra = próxima semana; traço preto = fim do
 * horizonte; linha tracejada = 100% da capacidade. Escala de 0 a 200%.
 */
export function FaixasCriticas({ faixas, horizonte }) {
  const lista = faixas.filter((f) => f.status !== "ok").slice(0, 6);
  const mostrar = lista.length ? lista : faixas.slice(0, 3);
  const pos = (v) => `${Math.min(v / 2, 1) * 100}%`;
  return (
    <ul className="faixas">
      {mostrar.map((f) => (
        <li key={`${f.dia}-${f.faixa}`}>
          <span>
            <strong style={{ textTransform: "capitalize" }}>{f.dia_nome}</strong>, {f.faixa_nome}
          </span>
          <StatusCapacidade status={f.status} />
          <div className="trilho" aria-hidden="true">
            <span className="agora" style={{ width: pos(f.ocupacao_proxima_semana) }} />
            <span className="futuro" style={{ left: pos(f.ocupacao_fim_horizonte) }} />
            <span className="limite" style={{ left: "50%" }} />
          </div>
          <span className="mudo" style={{ gridColumn: "1 / -1" }}>
            {pct(f.ocupacao_proxima_semana)} da capacidade na próxima semana, {pct(f.ocupacao_fim_horizonte)} em {horizonte} semanas
            {f.status === "atencao" && f.semanas_ate_exceder ? `. Deve passar do limite em cerca de ${f.semanas_ate_exceder} semanas.` : "."}
          </span>
        </li>
      ))}
    </ul>
  );
}

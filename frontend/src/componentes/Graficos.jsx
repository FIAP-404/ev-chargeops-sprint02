import { useId } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

// energia medida = vermelho da marca, no mesmo gradiente dos botões (ver estilos.css)
const GRADIENTE_ENERGIA = ["#ee3b2e", "#d7000f"];
const EIXO = { stroke: "#666666", fontSize: 12, tickLine: false, axisLine: false };
const GRADE = "#ececec";
const CURSOR = { fill: "rgba(230, 0, 18, 0.06)" };

function Dica({ active, payload, formatar }) {
  if (!active || !payload?.length) return null;
  const linha = payload[0].payload;
  return (
    <div className="tooltip-grafico">
      {formatar(linha).map((t, i) => (i === 0 ? <strong key={i}>{t}</strong> : <div key={i}>{t}</div>))}
    </div>
  );
}

/** Gradiente da barra: do coral (topo/início) ao vermelho (base). */
function Gradiente({ id, horizontal }) {
  const [inicio, fim] = GRADIENTE_ENERGIA;
  return (
    <defs>
      <linearGradient id={id} x1="0" y1="0" x2={horizontal ? "1" : "0"} y2={horizontal ? "0" : "1"}>
        <stop offset="0%" stopColor={horizontal ? fim : inicio} />
        <stop offset="100%" stopColor={horizontal ? inicio : fim} />
      </linearGradient>
    </defs>
  );
}

/** Barras verticais de uma só série (uma cor, sem eixo duplo). */
export function Barras({ dados, x, y, formatarX = (v) => v, formatarY = (v) => v, dica, altura = 220, cor }) {
  const id = `energia-${useId().replace(/:/g, "")}`;
  return (
    <ResponsiveContainer width="100%" height={altura}>
      <BarChart data={dados} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="22%">
        <Gradiente id={id} />
        <CartesianGrid vertical={false} stroke={GRADE} />
        <XAxis dataKey={x} tickFormatter={formatarX} {...EIXO} interval="preserveStartEnd" minTickGap={8} />
        <YAxis tickFormatter={formatarY} {...EIXO} width={52} />
        <Tooltip cursor={CURSOR} content={<Dica formatar={dica} />} />
        <Bar dataKey={y} fill={cor ?? `url(#${id})`} radius={[6, 6, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Barras horizontais: comparação entre unidades. */
export function BarrasHorizontais({ dados, categoria, valor, formatarValor = (v) => v, dica, cor }) {
  const id = `energia-${useId().replace(/:/g, "")}`;
  const altura = Math.max(dados.length * 30 + 30, 120);
  return (
    <ResponsiveContainer width="100%" height={altura}>
      <BarChart data={dados} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }} barCategoryGap="24%">
        <Gradiente id={id} horizontal />
        <CartesianGrid horizontal={false} stroke={GRADE} />
        <XAxis type="number" tickFormatter={formatarValor} {...EIXO} />
        <YAxis type="category" dataKey={categoria} {...EIXO} width={64} />
        <Tooltip cursor={CURSOR} content={<Dica formatar={dica} />} />
        <Bar dataKey={valor} fill={cor ?? `url(#${id})`} radius={[0, 6, 6, 0]} maxBarSize={20} />
      </BarChart>
    </ResponsiveContainer>
  );
}

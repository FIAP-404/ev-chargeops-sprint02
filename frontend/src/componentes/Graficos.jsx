import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const COR_DADO = "#2a78d6";
const EIXO = { stroke: "#6b7684", fontSize: 12, tickLine: false, axisLine: false };

function Dica({ active, payload, formatar }) {
  if (!active || !payload?.length) return null;
  const linha = payload[0].payload;
  return (
    <div className="tooltip-grafico">
      {formatar(linha).map((t, i) => (i === 0 ? <strong key={i}>{t}</strong> : <div key={i}>{t}</div>))}
    </div>
  );
}

/** Barras verticais de uma só série (uma cor, sem eixo duplo). */
export function Barras({ dados, x, y, formatarX = (v) => v, formatarY = (v) => v, dica, altura = 220, cor = COR_DADO }) {
  return (
    <ResponsiveContainer width="100%" height={altura}>
      <BarChart data={dados} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="22%">
        <CartesianGrid vertical={false} stroke="#e7ebef" />
        <XAxis dataKey={x} tickFormatter={formatarX} {...EIXO} interval="preserveStartEnd" minTickGap={8} />
        <YAxis tickFormatter={formatarY} {...EIXO} width={52} />
        <Tooltip cursor={{ fill: "rgba(42,120,214,0.08)" }} content={<Dica formatar={dica} />} />
        <Bar dataKey={y} fill={cor} radius={[4, 4, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Barras horizontais: comparação entre unidades. */
export function BarrasHorizontais({ dados, categoria, valor, formatarValor = (v) => v, dica, cor = COR_DADO }) {
  const altura = Math.max(dados.length * 30 + 30, 120);
  return (
    <ResponsiveContainer width="100%" height={altura}>
      <BarChart data={dados} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }} barCategoryGap="24%">
        <CartesianGrid horizontal={false} stroke="#e7ebef" />
        <XAxis type="number" tickFormatter={formatarValor} {...EIXO} />
        <YAxis type="category" dataKey={categoria} {...EIXO} width={64} />
        <Tooltip cursor={{ fill: "rgba(42,120,214,0.08)" }} content={<Dica formatar={dica} />} />
        <Bar dataKey={valor} fill={cor} radius={[0, 4, 4, 0]} maxBarSize={20} />
      </BarChart>
    </ResponsiveContainer>
  );
}

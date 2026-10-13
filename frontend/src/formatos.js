const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export const brl = (v) => moeda.format(v ?? 0);

export const num = (v, casas = 1) =>
  (v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });

export const kwh = (v, casas = 1) => `${num(v, casas)} kWh`;

export const pct = (v, casas = 0) => `${num(v * 100, casas)}%`;

export function dataHora(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function dataCurta(iso) {
  const d = new Date(iso + (iso.length === 10 ? "T12:00" : ""));
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}

const MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];

export function nomeMes(mes, curto = false) {
  const [ano, m] = mes.split("-").map(Number);
  const nome = MESES[m - 1];
  return curto ? `${nome.slice(0, 3)}/${String(ano).slice(2)}` : `${nome} de ${ano}`;
}

/** "outubro de 2026" -> "Outubro de 2026" (início de linha ou de tabela). */
export const nomeMesTitulo = (mes) => {
  const t = nomeMes(mes);
  return t[0].toUpperCase() + t.slice(1);
};

export function mesAtual() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function duracao(min) {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}

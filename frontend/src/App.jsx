import { useEffect, useState } from "react";
import { useApi } from "./api";
import AreaMorador from "./paginas/AreaMorador";
import Faturas from "./paginas/Faturas";
import PainelGestor from "./paginas/PainelGestor";
import Regras from "./paginas/Regras";
import SimularSessao from "./paginas/SimularSessao";

// navegação simples por hash (#/faturas), sem biblioteca de rotas
const PAGINAS = [
  ["painel", "Painel do gestor", PainelGestor],
  ["faturas", "Faturas", Faturas],
  ["morador", "Área do morador", AreaMorador],
  ["simular", "Simular recarga", SimularSessao],
  ["regras", "Regras de rateio", Regras],
];

function paginaDoHash() {
  const id = window.location.hash.replace("#/", "");
  return PAGINAS.some(([p]) => p === id) ? id : "painel";
}

export default function App() {
  const [pagina, setPagina] = useState(paginaDoHash());
  useEffect(() => {
    const aoMudar = () => setPagina(paginaDoHash());
    window.addEventListener("hashchange", aoMudar);
    return () => window.removeEventListener("hashchange", aoMudar);
  }, []);

  const Componente = PAGINAS.find(([p]) => p === pagina)[2];
  return (
    <div className="app">
      <aside className="lateral">
        <div className="marca">
          <svg width="34" height="34" viewBox="0 0 32 32" aria-hidden="true">
            <defs>
              <linearGradient id="marca-gradiente" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#ee3b2e" />
                <stop offset="100%" stopColor="#d7000f" />
              </linearGradient>
            </defs>
            <rect width="32" height="32" rx="10" fill="url(#marca-gradiente)" />
            <path d="M18 4 9 18h6l-2 10 10-15h-6z" fill="#fff" />
          </svg>
          <div>
            <strong><em>EV</em> ChargeOps</strong>
            <span>Residencial simulado</span>
          </div>
        </div>
        <nav className="nav" aria-label="Seções">
          {PAGINAS.map(([id, nome]) => (
            <a key={id} href={`#/${id}`} aria-current={pagina === id ? "page" : undefined}>{nome}</a>
          ))}
        </nav>
        <StatusCarregador pagina={pagina} />
      </aside>
      <main className="conteudo">
        <Componente key={pagina} />
      </main>
    </div>
  );
}

function StatusCarregador({ pagina }) {
  const { dados, recarregar } = useApi("/carregadores");
  useEffect(() => {
    const id = setInterval(recarregar, 10000);
    return () => clearInterval(id);
  }, [recarregar]);
  useEffect(recarregar, [pagina, recarregar]);
  const c = dados?.[0];
  if (!c) return null;
  const emUso = c.status === "em_uso";
  return (
    <div className="carregador-status" aria-live="polite">
      <div className="nome">{c.fabricante} {c.modelo}, {c.localizacao}</div>
      <div className="estado">
        <span className={`luz ${emUso ? "em-uso" : ""}`} aria-hidden="true" />
        {emUso ? "Em uso" : "Livre"}
      </div>
    </div>
  );
}

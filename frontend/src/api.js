import { useCallback, useEffect, useState } from "react";

// Em desenvolvimento o Vite repassa /api para o FastAPI (ver vite.config.js)
export const BASE = import.meta.env.VITE_API_URL || "/api";

export async function api(caminho, { metodo = "GET", corpo } = {}) {
  const resp = await fetch(BASE + caminho, {
    method: metodo,
    headers: corpo ? { "Content-Type": "application/json" } : undefined,
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  const dados = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detalhe = dados?.detail;
    const msg = typeof detalhe === "string" ? detalhe : detalhe ? "Dados inválidos" : `Erro ${resp.status}`;
    const erro = new Error(msg);
    erro.status = resp.status;
    throw erro;
  }
  return dados;
}

/** Busca dados de uma rota e devolve { dados, erro, carregando, recarregar }. */
export function useApi(caminho) {
  const [estado, setEstado] = useState({ dados: null, erro: null, carregando: true });

  const recarregar = useCallback(() => {
    if (!caminho) return;
    setEstado((e) => ({ ...e, carregando: true }));
    api(caminho)
      .then((dados) => setEstado({ dados, erro: null, carregando: false }))
      .catch((erro) => setEstado({ dados: null, erro, carregando: false }));
  }, [caminho]);

  useEffect(recarregar, [recarregar]);
  return { ...estado, recarregar };
}

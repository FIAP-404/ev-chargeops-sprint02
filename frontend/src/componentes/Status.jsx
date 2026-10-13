// Selos de status: a cor nunca aparece sozinha, sempre com ícone e texto.

const Icone = ({ tipo }) => {
  const props = { width: 12, height: 12, viewBox: "0 0 12 12", "aria-hidden": true };
  switch (tipo) {
    case "ok":
      return (<svg {...props}><path d="M2 6.5 5 9l5-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>);
    case "alerta":
      return (<svg {...props}><path d="M6 1 11 10.5H1z" fill="currentColor" /><path d="M6 4.5v2.6M6 8.6v.1" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" /></svg>);
    case "pergunta":
      return (<svg {...props}><circle cx="6" cy="6" r="5.5" fill="currentColor" /><path d="M4.6 4.6a1.5 1.5 0 1 1 1.9 1.4c-.4.2-.5.4-.5.9M6 8.7v.1" stroke="#fff" strokeWidth="1.3" fill="none" strokeLinecap="round" /></svg>);
    case "x":
      return (<svg {...props}><path d="m3 3 6 6M9 3 3 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>);
    case "relogio":
      return (<svg {...props}><circle cx="6" cy="6" r="4.8" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M6 3.5V6l1.8 1.2" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" /></svg>);
    default:
      return (<svg {...props}><rect x="2" y="2" width="8" height="8" rx="2" fill="currentColor" /></svg>);
  }
};

const COBRANCA = {
  liberada: { texto: "Liberada para cobrança", classe: "bom", icone: "ok" },
  faturada: { texto: "Faturada", classe: "", icone: "doc" },
  revisao_ia: { texto: "Retida pela IA", classe: "serio", icone: "alerta" },
  quarentena: { texto: "Sem identificação", classe: "atencao", icone: "pergunta" },
  descartada: { texto: "Não cobrada", classe: "", icone: "x" },
  aguardando: { texto: "Em andamento", classe: "", icone: "relogio" },
};

export function StatusCobranca({ status }) {
  const s = COBRANCA[status] || { texto: status, classe: "", icone: "doc" };
  return (
    <span className={`selo ${s.classe}`}>
      <Icone tipo={s.icone} />
      {s.texto}
    </span>
  );
}

const SEVERIDADE = {
  alta: { texto: "Alta", classe: "critico", icone: "alerta" },
  media: { texto: "Média", classe: "serio", icone: "alerta" },
  baixa: { texto: "Aviso", classe: "atencao", icone: "relogio" },
};

export function Severidade({ nivel }) {
  const s = SEVERIDADE[nivel] || SEVERIDADE.baixa;
  return (
    <span className={`selo ${s.classe}`}>
      <Icone tipo={s.icone} />
      {s.texto}
    </span>
  );
}

const CAPACIDADE = {
  acima_capacidade: { texto: "Acima da capacidade", classe: "critico", icone: "alerta" },
  atencao: { texto: "Atenção", classe: "atencao", icone: "relogio" },
  ok: { texto: "Folga", classe: "bom", icone: "ok" },
};

export function StatusCapacidade({ status }) {
  const s = CAPACIDADE[status];
  return (
    <span className={`selo ${s.classe}`}>
      <Icone tipo={s.icone} />
      {s.texto}
    </span>
  );
}

const FINAL = {
  concluida: "Concluída",
  interrompida: "Interrompida",
  falha: "Falha",
  em_andamento: "Em andamento",
};

export const textoStatusFinal = (s) => FINAL[s] || s;

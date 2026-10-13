# Roteiro do vídeo pitch — 5 minutos

O enunciado prevê que cada integrante grave, na prova presencial, um vídeo de 5 minutos que valida o desenvolvimento técnico. Este roteiro mostra o protótipo funcionando. Deixem o simulador rodado, a API e o front-end abertos antes de começar.

| Tempo | Tela | O que dizer |
|---|---|---|
| 0:00–0:35 | Slide de abertura (pitch da Sprint 01) | O problema: numa garagem compartilhada, sem medir quem usou, a energia do carregador vai para o rateio geral, e quem não tem carro elétrico paga junto. O EV ChargeOps registra cada sessão, faz um rateio transparente e usa IA para apoiar o gestor. |
| 0:35–1:05 | README, seção 3 (diagrama) | As quatro camadas da Sprint 01 implementadas: FastAPI, SQLite, React e IA com scikit-learn. Sem histórico de sessões no SEMS, usamos um simulador que chama a mesma API que um gateway do HCA G2 chamaria. |
| 1:05–2:05 | "Simular recarga" | Escolher a Marina (A-1204) e "Recarga normal": mostrar a telemetria chegando e a sessão liberada. Depois escolher o Paulo e "Cabo com defeito": a IA retém a cobrança e explica por quê (potência média muito abaixo da esperada para o carro). |
| 2:05–3:05 | "Painel do gestor" | Decisões pendentes: a sessão aparece com o motivo, a origem (regra ou modelo) e a sugestão. Liberar uma sessão com crédito de R$ 5. Mostrar a capacidade prevista: terça à noite já passa da capacidade e a procura cresce cerca de 3% por semana, o que justifica levar um segundo ponto à assembleia. |
| 3:05–3:50 | "Faturas" e depois "Área do morador" | A fatura explicável: cada parcela com a regra (kWh × tarifa, perdas, taxa, ociosidade). Unidade sem carro = R$ 0. Na área do morador: prévia do mês e projeção da IA. |
| 3:50–4:30 | `notebooks/avaliacao_ia.ipynb` | Como sabemos que funciona: o exemplo de R$ 61,90 reproduzido, e as regras sozinhas detectando 79% das anomalias contra 88% com o Isolation Forest, com 0,8% de alarmes falsos. A previsão de demanda erra 34% no total semanal contra 49% da média simples. |
| 4:30–5:00 | README, seção 8 | Desvios e próximos passos: validar o SEMS com a GoodWe/FIAP, recalibrar os modelos com sessões reais e adicionar autenticação. Fechamento: "Cada kWh com dono." |

## Dicas

- Cada integrante pode ser questionado sobre qualquer parte. Usem o `docs/guia-do-codigo.md` para estudar.
- Se algo falhar ao vivo, o notebook executado e as capturas em `docs/evidencias/` servem de evidência.
- Mostrem pelo menos uma linha de código: `encerrar_sessao()` em `backend/app/sessoes.py`, no trecho em que a IA decide o `status_cobranca`.

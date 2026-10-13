# Guia do código — EV ChargeOps Sprint 02

Guia de estudo para a equipe. O enunciado avalia se a equipe consegue explicar qualquer trecho do código, e a nota da sprint só sai depois da validação técnica no vídeo da prova presencial. Por isso, cada integrante deve conseguir percorrer este roteiro sozinho, com o código aberto.

Sugestão de estudo: rode o projeto (README, seção 2), abra `http://localhost:8000/docs` e siga uma sessão do começo ao fim com o roteiro abaixo.

## 1. Siga uma sessão de recarga pelo código

1. **O carregador lê o cartão.** A tela "Simular recarga" (`frontend/src/paginas/SimularSessao.jsx`) chama `POST /sessoes/iniciar`.
2. **A rota** (`backend/app/routers/sessoes.py`, função `iniciar`) valida o corpo com `schemas.IniciarSessao` e chama `sessoes.iniciar_sessao()`.
3. **`iniciar_sessao()`** (`backend/app/sessoes.py`):
   - gera o hash do cartão (`hash_rfid`) e procura em `CredencialRFID`;
   - se já existe sessão aberta no carregador, grava uma `TentativaOcupado` e lança `ErroSessao(409)`;
   - cria a `SessaoRecarga` com usuário, unidade e veículo (ou vazios, se o cartão não for conhecido) e grava o evento `inicio`.
4. **Telemetria a cada 15 min:** `registrar_telemetria()` guarda a última leitura válida do medidor, a potência máxima e, quando chega `carga_completa`, o horário em que a ociosidade começa.
5. **Encerramento:** `encerrar_sessao()` calcula `kwh_entregue = leitura_final − leitura_inicial`, a duração, os minutos ociosos e a potência média (kWh ÷ horas carregando). Depois decide a cobrança:
   - sem usuário → `quarentena` + alerta;
   - com usuário → `ia.avaliar_sessao_encerrada()` → `liberada` ou `revisao_ia`.
6. **O gestor decide** as retidas no painel (`DecisoesPendentes.jsx` → `POST /sessoes/{id}/revisao` → `revisar_sessao()`).
7. **Fechamento do mês:** `faturamento.gerar_faturas()` pega as sessões liberadas de cada unidade e chama `rateio.calcular_rateio()`.

## 2. Arquivo por arquivo (back-end)

| Arquivo | O que faz | Pontos para saber explicar |
|---|---|---|
| `database.py` | Cria a conexão e a sessão do SQLAlchemy | Por que `check_same_thread=False` (o FastAPI usa várias threads); como trocar para PostgreSQL (`EVCO_DB_URL`) |
| `models.py` | Tabelas | Diferença entre `status_final` (o que aconteceu fisicamente) e `status_cobranca` (se pode ser cobrado); por que existe `TentativaOcupado` |
| `sessoes.py` | Ciclo da sessão | Por que usamos leitura acumulada do medidor; o que acontece com leitura regressiva; como a falha de comunicação é reconciliada |
| `rateio.py` | Fórmula do rateio | Por que `Decimal` e não `float`; por que cada parcela é arredondada separadamente; por que o total nunca fica negativo |
| `faturamento.py` | Fechamento do mês | Fatura pode ser recalculada (é atualizada, não duplicada, graças ao `UniqueConstraint`); sessões pendentes ficam de fora |
| `ia/anomalias.py` | Regras + Isolation Forest | Como o Isolation Forest funciona; por que as variáveis são razões relativas ao veículo; o que é o percentil 97 |
| `ia/previsao.py` | Demanda e projeção | Por que Poisson (contagens); o que o termo de tendência representa; como a ocupação é estimada; a fórmula da mistura na projeção |
| `ia/servico.py` | Liga a IA ao fluxo | Onde a IA decide a cobrança; por que sessões apontadas não entram no treino; quando o modelo é retreinado |
| `integracoes/sems.py` | Adaptador GoodWe | O que está pronto e o que falta validar; por que credenciais vêm de variáveis de ambiente |
| `routers/*.py` | Rotas HTTP | `_executar()` traduz `ErroSessao` em resposta HTTP |
| `simulador/gerar_sessoes.py` | Gera os dados | Por que ele chama as funções do serviço em vez de inserir no banco; o que é o "roteiro de demonstração" |

## 3. Front-end

- `src/api.js`: `api()` faz o `fetch` e transforma erro HTTP em exceção; `useApi()` é um hook que busca e guarda `{dados, erro, carregando}`.
- `src/App.jsx`: navegação por hash (`#/faturas`), sem biblioteca de rotas; status do carregador atualizado a cada 10 s.
- `componentes/FaturaExplicavel.jsx`: desenha a memória de cálculo que veio do back-end. O front **não recalcula** a fatura.
- `componentes/DecisoesPendentes.jsx`: as ações do gestor. Um único botão primário (liberar), com o crédito como campo opcional e limitado ao valor da sessão (`valor_sessao`, calculado no back-end); "Não cobrar" é a saída de baixa ênfase; na quarentena, o gestor atribui o morador antes de liberar.
- `componentes/CampoReais.jsx`: campo de dinheiro no padrão dos apps bancários. Só aceita dígitos, os centavos entram pela direita e o valor fica em centavos inteiros.
- `componentes/PrevisaoCapacidade.jsx`: mapa de calor (rampa de um só tom de violeta, a cor das previsões) e barras de ocupação.
- `src/estilos.css`: tokens da identidade visual (vermelho da marca para ação e energia, violeta para a IA) e as regras do sistema de botões, documentadas no topo da seção de controles.

## 4. Conceitos de IA que a banca pode pedir

**Isolation Forest.** Constrói árvores que separam os dados com cortes aleatórios. Um ponto raro fica isolado com poucos cortes, e um ponto comum precisa de muitos. O score é a média de quantos cortes foram necessários. Usamos `decision_function` (negativo = anômalo) e convertemos em percentil do histórico para ter um número fácil de explicar: "mais atípica que 98% das sessões".

**Por que regras e modelo juntos?** As regras são auditáveis e valem desde o primeiro dia. O modelo pega combinações que não previmos. No notebook, as regras sozinhas detectam 79% das anomalias e, com o modelo, 88%. A diferença vem principalmente do cabo degradado.

**Regressão de Poisson.** Regressão linear sobre o logaritmo da média de uma contagem. Um coeficiente positivo na tendência quer dizer que a procura cresce um percentual fixo por semana.

**Backtest.** Treinar sem o período final e comparar a previsão com o que aconteceu de fato. Sempre comparamos com um baseline simples (média histórica, regra de três). Se o modelo não ganhar do baseline, não vale a complexidade.

**Cold start.** Com menos de 40 sessões, o Isolation Forest não é treinado e só as regras atuam.

## 5. Perguntas prováveis e respostas curtas

- **"Onde a IA é estrutural e não decorativa?"** Em `sessoes.encerrar_sessao()`: o resultado da IA define o `status_cobranca`. Sessão retida não entra em `gerar_faturas()`.
- **"Por que os dados são simulados?"** O SEMS não mostrou histórico de sessões por usuário na exploração da Sprint 01. O simulador usa o mesmo caminho da API, e o adaptador SEMS está pronto para quando houver acesso.
- **"Como vocês sabem que a IA funciona?"** O simulador grava um gabarito das anomalias injetadas, e o notebook mede quantas foram detectadas e quantos alarmes falsos houve, com e sem o modelo.
- **"Por que tirar o tempo ocioso do modelo?"** Na avaliação ele gerava alarmes em sessões normais. A ociosidade já é tratada por regra e por taxa no rateio.
- **"O que acontece se o gestor liberar uma sessão anômala?"** Ela entra na fatura, mas não entra no treino do modelo como exemplo de sessão normal.
- **"Como a unidade 1204 chega a R$ 61,90?"** 42 × 0,95 = 39,90; perdas 5% = 1,995 → 2,00; + 20,00 de taxa = 61,90 (`tests/test_rateio.py`).
- **"E se dois moradores chegarem ao mesmo tempo?"** O segundo recebe 409 e a tentativa é registrada. Ela entra na previsão de demanda como procura não atendida.

## 6. Exercícios para fixar (façam antes do vídeo)

1. Mude a tolerância de ociosidade para 60 min na tela "Regras de rateio", recalcule as faturas de um mês e explique a diferença.
2. Rode `python -m simulador.gerar_sessoes --seed 7` e veja como os alertas mudam.
3. Crie uma regra nova em `aplicar_regras()` (ex.: sessão com mais de 12 h) e um teste para ela em `tests/test_fluxo_sessao.py`.
4. Na tela "Simular recarga", gere uma sessão com cabo defeituoso e encontre, no código, a linha que a reteve.

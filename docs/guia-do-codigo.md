# Guia do código — EV ChargeOps Sprint 02

Guia de estudo para a equipe. Na validação técnica do vídeo, qualquer integrante pode ser perguntado sobre qualquer trecho. Cada um deve conseguir percorrer este roteiro sozinho, com o código aberto.

Sugestão de estudo: rode o projeto (README, seção 2), abra `http://localhost:8000/docs` e siga uma sessão do começo ao fim com o roteiro abaixo.

## 1. Siga uma sessão de recarga pelo código

1. **O carregador lê o cartão.** A tela "Simular recarga" (`frontend/src/paginas/SimularSessao.jsx`), com "Cartão RFID" marcado em "Identificação", chama `POST /sessoes/iniciar`.
   - **1b. Ou o morador lê o QR Code no app.** Com "App (QR Code)" marcado, a tela chama `POST /sessoes/iniciar-app` com o token do QR, o usuário e o veículo. A rota chama `sessoes.iniciar_sessao_app()`, que confere o QR (403 se inválido), o usuário (403 se inativo ou sem unidade) e o veículo (422 se for de outro usuário ou se faltar para quem tem dois). Depois chama a mesma `_abrir_sessao()` do RFID, com as mesmas validações (409 se o carregador estiver ocupado). O app nunca abre sessão anônima.
2. **A rota** (`backend/app/routers/sessoes.py`, função `iniciar`) valida o corpo com `schemas.IniciarSessao` e chama `sessoes.iniciar_sessao()`.
3. **`iniciar_sessao()`** (`backend/app/sessoes.py`):
   - gera o hash do cartão (`hash_rfid`) e procura em `CredencialRFID`;
   - chama `_abrir_sessao()`, que é comum ao RFID e ao app:
     - se já existe sessão aberta no carregador, grava uma `TentativaOcupado` e lança `ErroSessao(409)`;
     - recusa com 422 um início sobreposto a uma sessão já encerrada ou uma leitura inicial menor que a última do medidor (sem leitura inicial, continua de onde o medidor parou);
     - cria a `SessaoRecarga` com usuário, unidade, veículo e `metodo_identificacao` (ou vazios, se o cartão não for conhecido) e grava o evento `inicio`.
4. **Telemetria a cada 15 min:** `registrar_telemetria()` guarda a última leitura válida do medidor, a potência máxima e, quando chega `carga_completa`, o horário em que a ociosidade começa.
5. **Encerramento:** `encerrar_sessao()` calcula `kwh_entregue = leitura_final − leitura_inicial`, a duração, os minutos ociosos e a potência média (kWh ÷ horas carregando). Depois decide a cobrança:
   - sem usuário → `quarentena` + alerta;
   - com usuário → `ia.avaliar_sessao_encerrada()` → `liberada` ou `revisao_ia`.
   - **5b. Aviso ao morador:** ainda em `encerrar_sessao()`, se a sessão tem usuário, `notificacoes.notificar_resumo_sessao()` grava o resumo (e `notificar_ociosidade()` quando a IA aponta `ociosidade_longa`) na mesma transação; o envio passa pelo `Enviador` configurado, e uma falha no envio não desfaz o encerramento.
6. **O gestor decide** as retidas no painel (`DecisoesPendentes.jsx` → `POST /sessoes/{id}/revisao` → `revisar_sessao()`).
7. **Fechamento do mês:** `faturamento.gerar_faturas()` pega as sessões liberadas de cada unidade e chama `rateio.calcular_rateio()`. A rota `POST /faturas/gerar` retreina a IA logo depois.

Um carregador real entraria por `integracoes/ocpp16.py` (`processar`), que chama as mesmas três funções (`iniciar_sessao`, `registrar_telemetria`, `encerrar_sessao`).

## 2. Arquivo por arquivo (back-end)

| Arquivo | O que faz | Pontos para saber explicar |
|---|---|---|
| `database.py` | Cria a conexão e a sessão do SQLAlchemy | Por que `check_same_thread=False` (o FastAPI usa várias threads); como trocar para PostgreSQL (`EVCO_DB_URL`) |
| `models.py` | Tabelas | Diferença entre `status_final` (o que aconteceu fisicamente) e `status_cobranca` (se pode ser cobrado); por que existe `TentativaOcupado`; para que serve `metodo_identificacao` |
| `sessoes.py` | Ciclo da sessão | Por que usamos leitura acumulada do medidor; o que acontece com leitura regressiva; como a falha de comunicação é reconciliada; por que RFID e app passam pela mesma `_abrir_sessao()` |
| `rateio.py` | Fórmula do rateio | Por que `Decimal` e não `float`; por que cada parcela é arredondada separadamente; por que o total nunca fica negativo |
| `faturamento.py` | Fechamento do mês | Fatura pode ser recalculada (é atualizada, não duplicada, graças ao `UniqueConstraint`); sessões em revisão ou ainda abertas contam como pendências; regerar o mês reaproveita `memoria_calculo.parametros`; encerrar ou revisar uma sessão de mês já faturado marca a fatura como `desatualizada` |
| `notificacoes.py` | Avisos ao morador | O que vai no resumo da sessão; o `Enviador` (hoje `EnviadorConsole`) é onde entraria WhatsApp ou e-mail; por que a falha no envio é só registrada no log e não desfaz o encerramento |
| `ia/anomalias.py` | Regras + Isolation Forest | Como o Isolation Forest funciona; por que as variáveis são razões relativas ao veículo; o que é o percentil 97; por que uma sessão com potência dentro do esperado não é retida |
| `ia/previsao.py` | Demanda e projeção | Por que Poisson (contagens); o que o termo de tendência representa; como a ocupação é estimada; a fórmula da mistura na projeção |
| `ia/servico.py` | Liga a IA ao fluxo | Onde a IA decide a cobrança; por que sessões apontadas não entram no treino; quando o modelo é retreinado (ao gerar as faturas, por `POST /ia/treinar` e na subida da API sem modelo salvo) |
| `integracoes/sems.py` | Adaptador GoodWe | O que está pronto e o que falta validar; por que credenciais vêm de variáveis de ambiente |
| `integracoes/ocpp16.py` | Tradutor OCPP 1.6J | Frames CALL `[2, id, ação, payload]`, CALLRESULT `[3, ...]` e CALLERROR `[4, ...]`; energia chega em Wh e vira kWh; carregador ocupado responde `ConcurrentTx`; por que não usamos a biblioteca `ocpp` (é assíncrona e quer controlar a conexão) |
| `routers/ocpp.py` | Websocket `/ocpp/{carregador_id}` | Exige o subprotocolo `ocpp1.6`; o domínio é síncrono, então cada mensagem roda em `run_in_threadpool` |
| `routers/integracoes.py` | `POST /integracoes/sems/sincronizar` | Responde 503 sem as credenciais do SEMS e 502 quando o SEMS falha |
| `routers/notificacoes.py` | Caixa de avisos | Lista os avisos do morador (não lidos primeiro) e marca como lido |
| `routers/*.py` | Rotas HTTP | `_executar()` traduz `ErroSessao` em resposta HTTP |
| `simulador/gerar_sessoes.py` | Gera os dados | Por que ele chama as funções do serviço em vez de inserir no banco; o que é o "roteiro de demonstração"; por que parte das sessões começa pelo app |

## 3. Front-end

- `src/api.js`: `api()` faz o `fetch` e transforma erro HTTP em exceção; `useApi()` é um hook que busca e guarda `{dados, erro, carregando}`.
- `src/App.jsx`: navegação por hash (`#/faturas`), sem biblioteca de rotas; status do carregador atualizado a cada 10 s.
- `paginas/SimularSessao.jsx`: faz o papel do carregador. Em "Identificação", escolhe entre cartão RFID e App (QR Code); no modo app, mostra o adesivo do QR e o seletor de morador e de veículo.
- `paginas/Faturas.jsx`: lista as faturas do mês, avisa quando uma está `desatualizada` e exporta o CSV (`GET /faturas/exportar.csv`). O botão de imprimir usa `window.print()`, e as regras `@media print` de `estilos.css` deixam só a fatura na página (salvar como PDF).
- `paginas/AreaMorador.jsx`: prévia e projeção da fatura, recargas ("via app"/"via RFID") e a caixa de avisos, com "Marcar como lida".
- `paginas/PainelGestor.jsx`: decisões pendentes, capacidade prevista, consumo do mês e a tabela de consumo por carregador. Quando o modelo ainda não foi treinado, mostra o aviso de cold start com quantas sessões já existem.
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

**Cold start.** Com menos de 40 sessões elegíveis, o Isolation Forest não é treinado e só as regras atuam; o painel do gestor avisa e mostra quantas sessões já existem. Quando o histórico chega a 40, o modelo é treinado na próxima geração de faturas, por `POST /ia/treinar` ou na próxima subida da API (`treinar_se_necessario()`). A previsão de demanda precisa de 8 semanas completas de histórico.

## 5. Perguntas prováveis e respostas curtas

- **"Em que ponto a IA muda a cobrança?"** Em `sessoes.encerrar_sessao()`: o resultado da IA define o `status_cobranca`. Sessão retida não entra em `gerar_faturas()`.
- **"Por que os dados são simulados?"** O SEMS não mostrou histórico de sessões por usuário na exploração da Sprint 01. O simulador usa o mesmo caminho da API, e o adaptador SEMS está pronto para quando houver acesso.
- **"Como vocês sabem que a IA funciona?"** O simulador grava um gabarito das anomalias injetadas, e o notebook mede quantas foram detectadas e quantos alarmes falsos houve, com e sem o modelo.
- **"Por que tirar o tempo ocioso do modelo?"** Na avaliação ele gerava alarmes em sessões normais. A ociosidade já é tratada por regra e por taxa no rateio.
- **"O que acontece se o gestor liberar uma sessão anômala?"** Ela entra na fatura, mas não entra no treino do modelo como exemplo de sessão normal.
- **"Como a unidade 1204 chega a R$ 61,90?"** 42 × 0,95 = 39,90; perdas 5% = 1,995 → 2,00; + 20,00 de taxa = 61,90 (`tests/test_rateio.py`).
- **"E se dois moradores chegarem ao mesmo tempo?"** O segundo recebe 409 e a tentativa é registrada. Ela entra na previsão de demanda como procura não atendida.
- **"O QR Code pode ser falsificado?"** Pode ser copiado: o QR é fixo, e a API confia no `usuario_id` que o app envia, porque não há login. O próximo passo é um token rotativo ou assinado exibido no carregador, validado junto com um login real (README, seção 9).
- **"Por que não há fila de eventos?"** Com um carregador e uma leitura por minuto, uma fila só acrescentaria um processo e consistência eventual. No fluxo síncrono, a decisão da IA sai na resposta do encerramento. Cada mudança já grava um evento em `eventos_sessao` na mesma transação, e essa tabela pode ser consumida por um broker depois sem mudar o esquema.
- **"O OCPP foi testado num carregador?"** Não. Os testes (`tests/test_ocpp.py`) montam as mensagens à mão. O HCA G2 do laboratório é acessado pelo SEMS e não tivemos carregador ou gateway OCPP; o suporte do HCA G2 a OCPP ainda precisa ser confirmado com a GoodWe.

## 6. Exercícios para fixar (façam antes do vídeo)

1. Mude a tolerância de ociosidade para 60 min, simule uma recarga com ociosidade longa no mês atual e gere as faturas desse mês. Depois gere de novo um mês já fechado e explique por que o valor não muda (`memoria_calculo.parametros`).
2. Rode `python -m simulador.gerar_sessoes --seed 7` e veja como os alertas mudam.
3. Crie uma regra nova em `aplicar_regras()` (ex.: sessão com mais de 12 h) e um teste para ela em `tests/test_fluxo_sessao.py`.
4. Na tela "Simular recarga", gere uma sessão com cabo defeituoso e encontre, no código, a linha que a reteve.
5. No modo App (QR Code), a tela sempre envia o veículo escolhido. Em `http://localhost:8000/docs`, chame `POST /sessoes/iniciar-app` para um morador com dois veículos (a Marina, A-1204; o id está em `GET /usuarios`) com o QR `QR-HCA01-7F3A`, sem escolher o veículo, e encontre a linha que devolve 422.
6. Leia `tests/test_ocpp.py` e explique o frame de StopTransaction.

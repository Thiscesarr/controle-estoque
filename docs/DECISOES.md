# Registro de decisões técnicas

Cada decisão segue o formato: **contexto → alternativas → decisão → por quê →
consequências**. As consequências incluem o que se perde com a escolha, porque toda
decisão tem custo.

---

## 1. Expor a solução como API REST (e não app, site ou CLI)

- **Contexto:** o case deixa a forma de exposição livre e avalia modelagem, regras,
  consistência e testes, não interface gráfica.
- **Alternativas:** app mobile; site com front-end; programa de linha de comando (CLI); API REST.
- **Decisão:** API REST com FastAPI, usando o Swagger UI (gerado automaticamente) como
  interface de uso e demonstração.
- **Por quê:** é o formato de serviços de estoque reais (consumidos por outros sistemas),
  permite demonstrar operações concorrentes e não gasta o tempo do case com telas. App e
  site exigiriam uma segunda aplicação; a CLI não representa bem um serviço multiusuário.
- **Consequências:** quem for usar precisa de um cliente HTTP ou do navegador (Swagger). Não
  há tela própria para usuários finais.

## 2. FastAPI (e não Flask ou Django)

- **Alternativas:** Flask (mínimo, mas validação e documentação dependem de extensões);
  Django/DRF (completo, porém pesado para meia dúzia de rotas).
- **Decisão:** FastAPI.
- **Por quê:** valida a entrada a partir dos tipos (Pydantic), gera OpenAPI/Swagger sozinho,
  tem `TestClient` para testes e injeção de dependência simples.
- **Consequências:** dependência de um framework a mais. Mitigado porque o framework fica só na
  camada `api/`; o domínio não o conhece.

## 3. SQLite com `sqlite3` e SQL escrito à mão

- **Alternativas:** dicionário em memória; PostgreSQL; SQLAlchemy (ORM).
- **Decisão:** SQLite pelo módulo `sqlite3` da biblioteca padrão, com SQL explícito em um único
  arquivo (`repository.py`).
- **Por quê:** o case exige que operações inválidas não deixem estado inconsistente, e isso
  pede **transações**, que um dicionário não tem. O PostgreSQL exigiria Docker ou instalação
  para quem avaliar. Com poucas consultas, SQL à mão é mais transparente que um ORM: cada
  consulta pode ser lida e explicada.
- **Consequências:** o SQLite serializa as escritas e é um arquivo local (não serve para várias
  instâncias). O SQL está isolado no repositório, então migrar para PostgreSQL afeta só
  `db.py` e `repository.py`.

## 4. Disponível calculado (e não armazenado)

- **Contexto:** o produto tem quatro quantidades (total, disponível, reservada, bloqueada),
  e existem várias reservas e bloqueios simultâneos.
- **Alternativas:** guardar os quatro contadores na linha do produto e atualizá-los a cada
  operação.
- **Decisão:** guardar só o **total**. Reservado e bloqueado são a soma das retenções
  **ativas**, e o disponível é `total − reservado − bloqueado`.
- **Por quê:** um valor derivado que é armazenado pode divergir (um bug que atualiza um
  contador e esquece outro, uma falha no meio da operação). Calculando, a igualdade
  `total = disponível + reservado + bloqueado` vale **por construção**. As retenções
  precisariam existir de qualquer forma, para serem consultadas e liberadas pelo id.
- **Consequências:** cada leitura soma as retenções ativas (custo irrelevante nesta escala,
  com índice em `retencoes(sku, status)`). Em alto volume, eu manteria contadores na mesma
  transação, protegidos por `CHECK` (ver README, seção produção).

## 5. Reserva e bloqueio como um único modelo (`Retencao`)

- **Alternativas:** duas classes e duas tabelas (`Reserva`, `Bloqueio`).
- **Decisão:** um único modelo com campo `tipo` (`RESERVA` ou `BLOQUEIO`) e uma tabela
  `retencoes`. As rotas continuam separadas (`/reservas`, `/bloqueios`).
- **Por quê:** hoje os dois têm os mesmos dados e o mesmo comportamento (consomem o
  disponível e, ao serem liberados, devolvem a ele). Duas estruturas duplicariam código e
  testes.
- **Consequências:** se os comportamentos divergirem (ex.: reserva com expiração, bloqueio
  com descarte por perda), separar passa a valer a pena. Um id de bloqueio usado na rota de
  reservas é tratado como inexistente (404).

## 6. `solicitacao_id` obrigatório em reservas **e** bloqueios

- **Contexto:** o case diz: *"um produto pode possuir diversas reservas e bloqueios
  simultaneamente, inclusive associados a solicitações diferentes"*.
- **Decisão:** toda retenção guarda a solicitação que a originou (um pedido, um chamado de
  inspeção...). O id da retenção é único; o `solicitacao_id` pode se repetir.
- **Consequências:** permite rastrear por que cada unidade está retida.

## 7. Liberar muda o status (e não apaga o registro)

- **Alternativas:** `DELETE` da reserva/bloqueio.
- **Decisão:** a retenção passa de `ATIVA` para `LIBERADA` (com data); só as ativas entram
  no cálculo.
- **Por quê:** preserva o histórico e permite respostas precisas: id que nunca existiu → 404;
  id já liberado → 409. Com `DELETE`, os dois casos seriam iguais.
- **Consequências:** a tabela cresce com o tempo (em produção: arquivamento).

## 8. Rotas de ação: `POST /reservas/{id}/liberar`

- **Alternativas:** `DELETE /reservas/{id}`; `PATCH /reservas/{id}` com `{"status": "LIBERADA"}`.
- **Decisão:** `POST` em um sub-recurso de ação.
- **Por quê:** liberar é uma **transição de estado com efeito colateral** (devolve unidades e
  gera histórico), não uma remoção nem uma edição livre de campos. `DELETE` sugere que o
  recurso deixa de existir; `PATCH` permitiria pedir transições inválidas.

## 9. Uma transação por operação, com `BEGIN IMMEDIATE`

- **Contexto:** "consultar o saldo e depois gravar" é uma operação em duas etapas. Com
  pedidos simultâneos, dois podem ler o mesmo saldo e ambos reservarem a última unidade.
- **Alternativas:** lock em memória no Python (`threading.Lock`); transação `BEGIN` comum;
  sem transação.
- **Decisão:** cada operação de escrita abre `BEGIN IMMEDIATE`, que pega o lock de escrita do
  banco **antes** da primeira leitura, e faz `COMMIT` ou `ROLLBACK` ao final. Leituras usam
  uma transação comum para enxergar um retrato consistente.
- **Por quê:** o lock fica no banco, então funciona mesmo com vários processos da API. O lock
  em memória só protegeria um processo. Com `BEGIN` comum, duas transações podem ler juntas
  e depois disputar a escrita (no SQLite isso gera `database is locked`).
- **Evidência:** testes de mutação. Sem transação, 50 de 50 reservas simultâneas foram
  aceitas com só 10 unidades. Com `BEGIN` comum, as operações falharam com
  `database is locked`. Com `BEGIN IMMEDIATE`, exatamente 10 passam.
- **Consequências:** as escritas são serializadas (aceitável nesta escala). No PostgreSQL, o
  equivalente seria `SELECT ... FOR UPDATE` na linha do produto.

## 10. Journal padrão do SQLite (WAL descartado após medição)

- **Contexto:** a primeira versão ativou o modo WAL, pensando em concorrência entre leitura e
  escrita.
- **Medição:** com uma conexão por operação, o WAL faz *checkpoint* (gravação forçada em
  disco) sempre que a última conexão fecha. Na máquina de desenvolvimento, uma escrita levava
  ~320 ms com WAL contra ~70 ms no modo padrão, e a suíte de testes levava 132 s.
- **Decisão:** modo padrão de journal, com durabilidade padrão em execução normal. Nos testes,
  `synchronous = OFF`, que não altera transações, rollback ou locks.
- **Consequências:** suíte de 132 s para 1,5 s (na época, 68 testes). Leitores podem esperar
  brevemente por um escritor, o que não é relevante nesta escala.

## 11. Camadas: domínio, serviço, repositório e API

- **Alternativas:** tudo na rota (mais curto); Clean Architecture completa (interfaces e
  injeção de dependência em todas as camadas).
- **Decisão:** quatro camadas simples, com o domínio sem dependências externas.
- **Por quê:** separa o que muda por motivos diferentes (regra de negócio, SQL, HTTP) e deixa
  as regras testáveis sem banco. Interfaces abstratas para tudo seriam complexidade sem uso
  real neste momento.
- **Consequências:** mais arquivos do que uma solução de arquivo único, mas cada um com uma
  responsabilidade clara.

## 12. Erros de negócio com nome e código próprios

- **Decisão:** cada situação tem uma exceção (`QuantidadeIndisponivel`,
  `RetencaoJaLiberada`...) com `codigo` estável, e a conversão para HTTP fica em um único
  mapa (`api/app.py`).
- **Por quê:** quem chama identifica o erro pelo tipo, não pelo texto. O domínio não sabe que
  existe HTTP, então as regras servem para outros canais (fila, CLI). O formato
  `{"codigo", "mensagem"}` é o mesmo para erros de negócio e de validação.

## 13. Validação em duas camadas

- **Decisão:** o Pydantic valida o contrato HTTP (tipos, faixa, campos extras proibidos,
  `strict` para não converter `"3"` em `3`). O domínio valida de novo (quantidade, SKU,
  textos).
- **Por quê:** a API rejeita cedo o que está fora do contrato, e o domínio continua protegido
  se for chamado por outro caminho. Exemplo: `"   "` passa no `min_length` do Pydantic, mas é
  barrado pelo domínio.
- **Detalhe:** `True` é recusado como quantidade, porque em Python `bool` é subtipo de `int`
  (vale 1).

## 14. Quantidades inteiras com limite por operação

- **Decisão:** inteiros de 1 a 1.000.000 por operação.
- **Por quê:** estoque conta unidades discretas, e `float` traz erros de arredondamento
  (`0.1 + 0.2 ≠ 0.3`). O limite protege contra erros de digitação e mantém os totais longe do
  limite de 64 bits do banco.

## 15. Trilha de auditoria (`movimentacoes`)

- **Decisão:** toda operação bem-sucedida grava uma movimentação na **mesma transação**.
- **Por quê:** responde "por que o estoque está assim?". Por estar na mesma transação, não
  existe movimento sem registro nem registro sem movimento.

## 16. Relógio e gerador de ids injetáveis no serviço

- **Decisão:** `ServicoEstoque(banco, relogio=..., gerador_id=...)`, com padrões reais (UTC e
  UUID4).
- **Por quê:** nos testes, datas e ids ficam fixos e os resultados exatos podem ser
  verificados. `Retencao.liberar(em)` recebe o instante pelo mesmo motivo.

## 17. Estratégia de testes

- **Decisão:** unitários (regras), integração (banco real, rollback, persistência), API
  (contrato), concorrência (threads) e propriedades (Hypothesis com modelo de referência), com
  CI em Python 3.11 e 3.13.
- **Por quê:** cada nível pega um tipo de erro diferente. Os testes de concorrência e de
  propriedades cobrem o que exemplos escritos à mão não alcançam. A mutação provou que os
  testes de concorrência detectam a ausência de controle de concorrência.

# Controle de Estoque

[![CI](https://github.com/Thiscesarr/controle-estoque/actions/workflows/ci.yml/badge.svg)](https://github.com/Thiscesarr/controle-estoque/actions/workflows/ci.yml)

API REST em Python para controle de estoque de produtos com **entrada**, **saída**,
**reservas**, **bloqueios**, **liberação** e **consulta**, garantindo que o estoque nunca
fique inconsistente, inclusive com várias operações acontecendo ao mesmo tempo.

## Sumário

- [Visão geral](#visão-geral)
- [Como executar](#como-executar)
- [Arquitetura e estrutura](#arquitetura-e-estrutura)
- [Principais decisões técnicas](#principais-decisões-técnicas)
- [Premissas](#premissas)
- [Limitações conhecidas](#limitações-conhecidas)
- [Estratégia de testes](#estratégia-de-testes)
- [Em produção, o que eu faria diferente](#em-produção-o-que-eu-faria-diferente)
- [Uso de IA](#uso-de-ia)
- [Visão de engenharia: 5 aspectos mais importantes](#visão-de-engenharia-5-aspectos-mais-importantes)

---

## Visão geral

Para cada produto o sistema informa **quantidade total**, **disponível**, **reservada** e
**bloqueada**. A regra central é:

```
disponível = total − reservado − bloqueado        e        disponível ≥ 0 sempre
```

Só o **total** é armazenado. Reservas e bloqueios são registros próprios (com id, quantidade
e solicitação de origem), e o reservado, o bloqueado e o disponível são **calculados** a
partir deles. Assim não existe um número guardado que possa ficar desatualizado.

### Exemplo de uso (centro de distribuição)

| Passo | Operação | Total | Disponível | Reservado | Bloqueado |
|---|---|---|---|---|---|
| 1 | Cadastro do produto `NB-001` | 0 | 0 | 0 | 0 |
| 2 | Entrada de 10 unidades | 10 | 10 | 0 | 0 |
| 3 | Reserva de 3 para o pedido `PED-1` | 10 | 7 | 3 | 0 |
| 4 | Bloqueio de 2 (caixa avariada) | 10 | 5 | 3 | 2 |
| 5 | Saída de 6 → **recusada (409)**: só há 5 disponíveis | 10 | 5 | 3 | 2 |
| 6 | Liberação da reserva | 10 | 8 | 0 | 2 |
| 7 | Liberação do bloqueio | 10 | 10 | 0 | 0 |
| 8 | Saída de 4 | 6 | 6 | 0 | 0 |

Esse fluxo completo é um dos testes automatizados (`tests/api/test_api_estoque.py`).

### Funcionalidades (endpoints)

| Funcionalidade | Método e rota | Sucesso | Erros |
|---|---|---|---|
| Cadastrar produto | `POST /produtos` | 201 | 409, 422 |
| **Consultar** situação | `GET /produtos/{sku}` | 200 | 404 |
| **Entrada** | `POST /produtos/{sku}/entradas` | 200 | 404, 422 |
| **Saída** | `POST /produtos/{sku}/saidas` | 200 | 404, 409, 422 |
| **Reservar** | `POST /produtos/{sku}/reservas` | 201 | 404, 409, 422 |
| Consultar reserva | `GET /reservas/{id}` | 200 | 404 |
| **Liberar** reserva | `POST /reservas/{id}/liberar` | 200 | 404, 409 |
| **Bloquear** | `POST /produtos/{sku}/bloqueios` | 201 | 404, 409, 422 |
| Consultar bloqueio | `GET /bloqueios/{id}` | 200 | 404 |
| **Liberar** bloqueio | `POST /bloqueios/{id}/liberar` | 200 | 404, 409 |
| Histórico (auditoria) | `GET /produtos/{sku}/movimentacoes` | 200 | 404 |
| Saúde da API | `GET /saude` | 200 | — |

Todo erro tem o mesmo formato:

```json
{ "codigo": "QUANTIDADE_INDISPONIVEL", "mensagem": "Quantidade solicitada (6) maior que a disponível (5)" }
```

| Status | Significado | Códigos |
|---|---|---|
| 404 | Recurso não existe | `PRODUTO_NAO_ENCONTRADO`, `RETENCAO_NAO_ENCONTRADA` |
| 409 | Conflito com o estado atual | `QUANTIDADE_INDISPONIVEL`, `PRODUTO_JA_CADASTRADO`, `RETENCAO_JA_LIBERADA` |
| 422 | Dado inválido | `REQUISICAO_INVALIDA`, `DADO_INVALIDO`, `QUANTIDADE_INVALIDA` |

---

## Como executar

### Pré-requisitos

- Python **3.11 ou superior**
- Git

### 1. Clonar e preparar o ambiente

```bash
git clone https://github.com/Thiscesarr/controle-estoque.git
cd controle-estoque
python -m venv .venv
```

Ativar o ambiente virtual:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate
```

Instalar o projeto com as ferramentas de desenvolvimento:

```bash
pip install -e ".[dev]"
```

### 2. Subir a API

```bash
uvicorn estoque.main:app --reload
```

- Documentação interativa (Swagger): **http://localhost:8000/docs**. Cada rota tem o botão
  *Try it out* para testar direto pelo navegador.
- O banco é o arquivo `estoque.db`, criado automaticamente na pasta atual. Para usar outro
  caminho, defina a variável de ambiente `ESTOQUE_DB`.

### 3. Exemplo pelo terminal

> No Windows PowerShell, use `curl.exe` no lugar de `curl`.

```bash
curl -X POST localhost:8000/produtos -H "Content-Type: application/json" -d '{"sku": "NB-001", "nome": "Notebook X"}'
curl -X POST localhost:8000/produtos/NB-001/entradas -H "Content-Type: application/json" -d '{"quantidade": 10}'
curl -X POST localhost:8000/produtos/NB-001/reservas -H "Content-Type: application/json" -d '{"quantidade": 3, "solicitacao_id": "PED-1"}'
curl localhost:8000/produtos/NB-001
```

### 4. Rodar os testes e as verificações

```bash
pytest                                   # todos os testes
pytest --cov=estoque                     # com relatório de cobertura
ruff check .                             # lint
ruff format --check .                    # formatação
```

---

## Arquitetura e estrutura

Arquitetura em camadas, com o domínio no centro e sem dependência de framework:

```
 Requisição HTTP
       │
       ▼
┌──────────────────┐   valida o JSON (Pydantic) e traduz erros em status HTTP
│  api/            │   routes.py · schemas.py · app.py
└────────┬─────────┘
         ▼
┌──────────────────┐   orquestra cada operação: abre a transação, lê, aplica a regra,
│  service.py      │   grava e registra a movimentação
└───┬──────────┬───┘
    ▼          ▼
┌────────┐ ┌──────────────────┐
│ domain/│ │  repository.py   │   todo o SQL
│ regras │ └────────┬─────────┘
│ puras  │          ▼
└────────┘ ┌──────────────────┐
           │  db.py + SQLite  │   conexões, transações e schema
           └──────────────────┘
```

As dependências apontam para dentro: a API conhece o serviço, o serviço conhece o domínio e
o repositório, e o **domínio não conhece ninguém**. Trocar o FastAPI por uma fila de
mensagens, ou o SQLite por PostgreSQL, não exige mexer nas regras de negócio.

### Caminho de uma reserva, passo a passo

1. `POST /produtos/NB-001/reservas` chega em `api/routes.py`. O Pydantic valida o corpo
   (`api/schemas.py`); se inválido, responde 422 sem tocar no banco.
2. A rota chama `ServicoEstoque.reservar` (`service.py`), que valida os dados no domínio e
   abre uma transação de escrita (`BEGIN IMMEDIATE`, em `db.py`).
3. O serviço lê produto + saldo (`repository.py`, uma única consulta SQL), e o domínio
   verifica se há disponível suficiente (`SaldoEstoque.garantir_disponivel`).
4. Grava a reserva e a movimentação de auditoria e confirma a transação (`COMMIT`).
5. Qualquer erro no caminho faz `ROLLBACK`: nada é gravado. Erros de negócio viram 404/409/422
   em `api/app.py`.

### Onde está cada coisa

| Arquivo | Responsabilidade |
|---|---|
| `src/estoque/domain/models.py` | Modelos (`Produto`, `SaldoEstoque`, `Retencao`, `Movimentacao`...) e regras puras (validações, disponível, liberação) |
| `src/estoque/domain/errors.py` | Erros de negócio com código estável (`QuantidadeIndisponivel`, `RetencaoJaLiberada`...) |
| `src/estoque/service.py` | Casos de uso: cadastrar, consultar, entrada, saída, reservar, bloquear, liberar, histórico |
| `src/estoque/repository.py` | Todo o SQL: leitura do saldo, gravação de retenções e movimentações |
| `src/estoque/db.py` | Schema das tabelas, conexões e transações (`BEGIN IMMEDIATE`, `COMMIT`, `ROLLBACK`) |
| `src/estoque/api/schemas.py` | Contratos de entrada e saída da API (validação com Pydantic) |
| `src/estoque/api/routes.py` | Endpoints HTTP (finos, sem regra de negócio) |
| `src/estoque/api/app.py` | Montagem da aplicação e tradução de erros para status HTTP |
| `src/estoque/main.py` | Ponto de entrada do servidor (`uvicorn estoque.main:app`) |
| `tests/unitarios/` | Regras puras do domínio, sem banco |
| `tests/integracao/` | Serviço + SQLite real: regras, rollback, persistência, concorrência e propriedades |
| `tests/api/` | Contrato HTTP: status, formato de erro, validação de entrada |
| `docs/DECISOES.md` | Registro detalhado de cada decisão técnica e das alternativas descartadas |
| `docs/USO_DE_IA.md` | Prompts principais e análise crítica do uso de IA |
| `.github/workflows/ci.yml` | Integração contínua: lint, formatação e testes em Python 3.11 e 3.13 |

### Modelo de dados (SQLite)

| Tabela | Conteúdo |
|---|---|
| `produtos` | `sku` (chave), `nome`, `quantidade_total` (com `CHECK >= 0`) |
| `retencoes` | reservas e bloqueios: `id`, `sku`, `tipo`, `quantidade`, `solicitacao_id`, `motivo`, `status` (`ATIVA`/`LIBERADA`), datas |
| `movimentacoes` | histórico imutável de cada operação (auditoria) |

---

## Principais decisões técnicas

Cada decisão está detalhada, com alternativas e consequências, em
[`docs/DECISOES.md`](docs/DECISOES.md). Em resumo:

1. **Disponível calculado, não armazenado.** Elimina a chance de os números divergirem; a
   regra `total = disponível + reservado + bloqueado` vale por construção.
2. **Reserva e bloqueio como um único modelo (`Retencao`)**, diferenciados por `tipo`: têm os
   mesmos dados e o mesmo comportamento hoje.
3. **Liberar não apaga.** A retenção muda para `LIBERADA`, preservando o histórico e
   diferenciando "não existe" (404) de "já liberada" (409).
4. **Uma transação por operação, com `BEGIN IMMEDIATE`.** As escritas acontecem uma de cada
   vez; duas reservas simultâneas nunca leem o mesmo saldo (sem *overselling*).
5. **SQLite com SQL escrito à mão**: transações ACID reais sem nenhuma instalação extra, e
   cada consulta é explícita e explicável.
6. **FastAPI**: validação automática de entrada e documentação Swagger gerada a partir do
   código.
7. **Domínio isolado e erros de negócio com nome próprio**, traduzidos para HTTP em um
   único lugar.
8. **Trilha de auditoria** (`movimentacoes`) gravada na mesma transação da operação.
9. **Validação em duas camadas**: Pydantic no contrato HTTP e o próprio domínio, que se
   protege venha a chamada de onde vier.

---

## Premissas

- Quantidades são **unidades inteiras**, de 1 a 1.000.000 por operação.
- **Total** = unidades fisicamente no estoque. **Saída** só consome o disponível: unidades
  reservadas ou bloqueadas não saem diretamente.
- Toda reserva e todo bloqueio pertencem a uma **solicitação** (`solicitacao_id`
  obrigatório). Uma mesma solicitação pode ter várias retenções, de produtos diferentes ou
  do mesmo produto.
- **Liberar** devolve a quantidade inteira ao disponível. Uma retenção é liberada uma única
  vez e não há liberação parcial.
- O produto precisa ser **cadastrado** antes de receber entradas (evita produtos criados por
  erro de digitação no SKU).
- O **SKU** diferencia maiúsculas de minúsculas e aceita letras, números, `.`, `-` e `_` (ele
  faz parte da URL).
- Um único depósito/local de estoque. Datas registradas em UTC.

## Limitações conhecidas

- **Sem "efetivar reserva".** Para entregar um pedido reservado, hoje é preciso liberar a
  reserva e registrar a saída em duas chamadas. Entre elas, outra operação poderia usar
  as unidades. A evolução natural é `POST /reservas/{id}/efetivar`, que numa única
  transação baixaria o total e marcaria a reserva como `EFETIVADA`.
- **Sem idempotência.** Se o cliente reenviar uma entrada por timeout, ela é aplicada duas
  vezes.
- **Sem expiração de reservas.** Uma reserva esquecida retém o estoque até ser liberada.
- **SQLite serializa todas as escritas** e vive em um arquivo local: adequado para uma
  instância, mas não para várias instâncias da API em paralelo.
- Sem autenticação/autorização, sem paginação no histórico e sem migrations versionadas
  (as tabelas são criadas com `CREATE TABLE IF NOT EXISTS`).

---

## Estratégia de testes

**103 testes, cobertura de 99%**, rodando em cerca de 10 segundos. Organizados por camada,
do mais rápido e específico ao mais amplo:

| Tipo | Onde | O que garante |
|---|---|---|
| Unitários | `tests/unitarios/test_regras_dominio.py` | Regras puras: validações (inclusive `True`, `3.0`, `"3"`), cálculo do disponível, liberação única |
| Integração | `tests/integracao/test_servico_estoque.py` | Regras do case com banco real; falha no meio da operação **não deixa rastro** (rollback); dados persistem |
| API | `tests/api/test_api_estoque.py` | Status HTTP, formato de erro, validação do contrato, fluxo completo do case |
| Concorrência | `tests/integracao/test_concorrencia.py` | 50 reservas simultâneas com 10 unidades: **exatamente 10** passam; liberação simultânea devolve uma única vez |
| Propriedades | `tests/integracao/test_propriedades.py` | O Hypothesis gera sequências aleatórias de operações e compara, a cada passo, o sistema com um modelo de referência |

**Os testes de concorrência foram validados por mutação.** Removendo de propósito as
transações do `db.py`, os testes falham mostrando exatamente o problema que previnem: 50 de
50 reservas aceitas com apenas 10 unidades, a mesma reserva liberada 10 vezes, e o total
alterado após uma falha. Trocando `BEGIN IMMEDIATE` por `BEGIN`, as operações simultâneas
falham com `database is locked`. Ou seja, os testes realmente detectam a falta de controle
de concorrência.

Nos testes, o banco roda com `synchronous = OFF` (sem esperar a gravação física no disco a
cada commit). Isso não altera transações, rollback nem locks; só a proteção contra queda de
energia, irrelevante para um banco temporário. Na máquina de desenvolvimento, a suíte (então
com 68 testes) caiu de 132 s para 1,5 s. Em execução normal, a durabilidade padrão do SQLite é
mantida.

---

## Em produção, o que eu faria diferente

- **Banco**: PostgreSQL, com lock na linha do produto (`SELECT ... FOR UPDATE`) ou controle
  otimista (coluna `versao`), permitindo várias instâncias da API. Com alto volume,
  manteria contadores de reservado/bloqueado na própria linha do produto, atualizados na
  mesma transação e protegidos por `CHECK`, para leitura sem somatório.
- **Migrations versionadas** (ex.: Alembic) em vez de `CREATE TABLE IF NOT EXISTS`.
- **Idempotência**: cabeçalho `Idempotency-Key` nos `POST`, para que reenvios não dupliquem
  operações.
- **Novas regras**: efetivar reserva, expiração automática de reservas, liberação parcial.
- **Segurança**: autenticação (OAuth2/JWT), autorização por perfil e registro de quem fez
  cada movimentação.
- **Observabilidade**: logs estruturados com id de correlação, métricas (latência, taxa de
  409 por produto), tracing (OpenTelemetry) e *readiness check* que testa o banco.
- **Integração**: publicação de eventos de estoque (padrão *outbox*) para outros sistemas.
- **Entrega**: imagem Docker, versões de dependências travadas (*lock file*), atualização
  automática de dependências e análise de segurança no CI, testes de carga.

---

## Uso de IA

O desenvolvimento foi feito com o **Claude** (Anthropic), usado como par de programação. Os
principais prompts, onde a IA mais ajudou, o que modifiquei e os erros identificados estão
em [`docs/USO_DE_IA.md`](docs/USO_DE_IA.md).

---

## Visão de engenharia: 5 aspectos mais importantes

1. **Consistência transacional.** Cada operação é atômica: ou acontece por inteiro (estoque
   + histórico), ou não acontece. Em estoque, um estado parcial vira prejuízo (venda sem
   produto, produto parado sem motivo).
2. **Controle de concorrência.** Em produção, pedidos chegam ao mesmo tempo. Sem lock ou
   versionamento, duas operações leem o mesmo saldo e vendem a mesma unidade. É o ponto em
   que um sistema "funciona no teste" e falha no mundo real.
3. **Testes automatizados focados em regras e invariantes, rodando no CI.** Testes de
   regra, de concorrência e de propriedades permitem mudar o código com segurança, e o CI
   impede que algo quebrado chegue à branch principal.
4. **Rastreabilidade e observabilidade.** O histórico de movimentações responde "por que o
   estoque está assim?". Logs, métricas e tracing respondem "o que está acontecendo agora?".
   Sem isso, investigar uma divergência vira adivinhação.
5. **Baixo acoplamento e contratos estáveis.** Domínio isolado, erros com código estável e
   API documentada permitem evoluir (novas regras, outro banco, outro canal de entrada) sem
   reescrever o sistema nem quebrar quem já o utiliza.

# Controle de Estoque

[![CI](https://github.com/Thiscesarr/controle-estoque/actions/workflows/ci.yml/badge.svg)](https://github.com/Thiscesarr/controle-estoque/actions/workflows/ci.yml)

API REST em Python para controle de estoque com **entrada**, **saída**, **reservas**,
**bloqueios**, **liberação**, **efetivação de reservas** e **consulta**, garantindo que o
estoque nunca fique inconsistente, inclusive com várias operações ao mesmo tempo.

## Sumário

**Documentação**
1. [Visão geral da solução](#1-visão-geral-da-solução)
2. [Arquitetura e estrutura](#2-arquitetura-e-estrutura)
3. [Principais decisões técnicas](#3-principais-decisões-técnicas)
4. [Premissas](#4-premissas)
5. [Limitações conhecidas](#5-limitações-conhecidas)
6. [Instruções para execução](#6-instruções-para-execução)
7. [Em produção, o que eu faria diferente](#7-em-produção-o-que-eu-faria-diferente)

**Uso de IA e visão de engenharia**

8. [Uso de IA](#8-uso-de-ia)
9. [Visão de engenharia: 5 aspectos mais importantes](#9-visão-de-engenharia-5-aspectos-mais-importantes)

---

## 1. Visão geral da solução

Para cada produto, o sistema informa **total**, **disponível**, **reservado** e
**bloqueado**. A regra central:

```
disponível = total − reservado − bloqueado        e        disponível ≥ 0 sempre
```

Só o **total** é armazenado. Reservas e bloqueios são registros próprios (com id,
quantidade e solicitação de origem); o reservado e o bloqueado são a soma dos registros
**ativos**, e o disponível é **calculado**. Não existe número guardado que possa ficar
desatualizado.

Uma reserva termina de uma de duas formas: **liberada** (cancelamento: as unidades voltam ao
disponível) ou **efetivada** (pedido entregue: as unidades saem do estoque, numa única
transação).

### Exemplo (centro de distribuição)

| Passo | Operação | Total | Disponível | Reservado | Bloqueado |
|---|---|---|---|---|---|
| 1 | Cadastro do produto `NB-001` | 0 | 0 | 0 | 0 |
| 2 | Entrada de 10 unidades | 10 | 10 | 0 | 0 |
| 3 | Reserva de 3 para o pedido `PED-1` | 10 | 7 | 3 | 0 |
| 4 | Bloqueio de 2 (caixa avariada) | 10 | 5 | 3 | 2 |
| 5 | Saída de 6 → **recusada (409)**: só há 5 disponíveis | 10 | 5 | 3 | 2 |
| 6 | Pedido `PED-1` entregue: **efetivação** da reserva | 7 | 5 | 0 | 2 |
| 7 | Liberação do bloqueio (inspeção aprovou) | 7 | 7 | 0 | 0 |

### Endpoints

| Funcionalidade | Método e rota | Sucesso | Erros |
|---|---|---|---|
| Cadastrar produto | `POST /produtos` | 201 | 409, 422 |
| **Consultar** situação | `GET /produtos/{sku}` | 200 | 404 |
| **Entrada** | `POST /produtos/{sku}/entradas` | 200 | 404, 422 |
| **Saída** | `POST /produtos/{sku}/saidas` | 200 | 404, 409, 422 |
| **Reservar** | `POST /produtos/{sku}/reservas` | 201 | 404, 409, 422 |
| Consultar reserva | `GET /reservas/{id}` | 200 | 404 |
| **Liberar** reserva | `POST /reservas/{id}/liberar` | 200 | 404, 409 |
| **Efetivar** reserva | `POST /reservas/{id}/efetivar` | 200 | 404, 409 |
| **Bloquear** | `POST /produtos/{sku}/bloqueios` | 201 | 404, 409, 422 |
| Consultar bloqueio | `GET /bloqueios/{id}` | 200 | 404 |
| **Liberar** bloqueio | `POST /bloqueios/{id}/liberar` | 200 | 404, 409 |
| Histórico (auditoria) | `GET /produtos/{sku}/movimentacoes` | 200 | 404 |
| Saúde da API | `GET /saude` | 200 | — |

Todo erro, de negócio, de validação ou de rota, tem o mesmo formato:

```json
{ "codigo": "QUANTIDADE_INDISPONIVEL", "mensagem": "Quantidade solicitada (6) maior que a disponível (5)" }
```

| Status | Significado | Códigos |
|---|---|---|
| 404 | Recurso ou rota não existe | `PRODUTO_NAO_ENCONTRADO`, `RETENCAO_NAO_ENCONTRADA`, `ROTA_NAO_ENCONTRADA` |
| 405 | Método errado para a rota | `METODO_NAO_PERMITIDO` |
| 409 | Conflito com o estado atual | `QUANTIDADE_INDISPONIVEL`, `PRODUTO_JA_CADASTRADO`, `RETENCAO_JA_LIBERADA`, `RETENCAO_JA_EFETIVADA` |
| 422 | Dado inválido | `REQUISICAO_INVALIDA`, `DADO_INVALIDO`, `QUANTIDADE_INVALIDA` |

---

## 2. Arquitetura e estrutura

Camadas, com o domínio no centro e sem dependência de framework:

```
 Requisição HTTP
       │
       ▼
┌──────────────────┐   valida o JSON (Pydantic) e traduz erros em status HTTP
│  api/            │   routes.py · schemas.py · app.py
└────────┬─────────┘
         ▼
┌──────────────────┐   executa cada operação: abre a transação, lê, aplica a regra,
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

O **domínio não conhece banco nem HTTP**. Trocar o FastAPI por uma fila de mensagens, ou o
SQLite por PostgreSQL, não mexe nas regras de negócio.

### Caminho de uma reserva

1. `POST /produtos/NB-001/reservas` chega em `api/routes.py`. O Pydantic valida o corpo; se
   for inválido, responde 422 sem tocar no banco.
2. `ServicoEstoque.reservar` valida os dados no domínio e abre uma transação de escrita
   (`BEGIN IMMEDIATE`, em `db.py`), que pega o lock antes de ler.
3. O repositório lê produto + saldo em uma única consulta SQL, e o domínio verifica se há
   disponível suficiente (`SaldoEstoque.garantir_disponivel`).
4. Grava a reserva e a movimentação de auditoria e faz `COMMIT`.
5. Qualquer erro no caminho faz `ROLLBACK`: nada é gravado. O erro vira 404/409/422 em
   `api/app.py`.

### Onde está cada coisa

| Arquivo | Responsabilidade |
|---|---|
| `src/estoque/domain/models.py` | Modelos (`Produto`, `SaldoEstoque`, `Retencao`, `Movimentacao`) e regras puras: validações, disponível, liberação e efetivação |
| `src/estoque/domain/errors.py` | Erros de negócio com código estável |
| `src/estoque/service.py` | Casos de uso: cadastrar, consultar, entrada, saída, reservar, bloquear, liberar, efetivar, histórico |
| `src/estoque/repository.py` | Todo o SQL |
| `src/estoque/db.py` | Schema, conexões e transações (`BEGIN IMMEDIATE`, `COMMIT`, `ROLLBACK`) |
| `src/estoque/api/schemas.py` | Contratos de entrada e saída (Pydantic) |
| `src/estoque/api/routes.py` | Endpoints HTTP, sem regra de negócio |
| `src/estoque/api/app.py` | Montagem da aplicação e tradução de erros para status HTTP |
| `src/estoque/main.py` | Ponto de entrada (`uvicorn estoque.main:app`) |
| `tests/` | Testes unitários, de integração, de API, de concorrência e de propriedades |
| `docs/DECISOES.md` | Cada decisão técnica com alternativas e consequências |
| `docs/USO_DE_IA.md` | Prompts principais e análise do uso de IA |
| `.github/workflows/ci.yml` | CI: lint, formatação e testes em Python 3.11 e 3.13 |

### Modelo de dados (SQLite)

| Tabela | Conteúdo |
|---|---|
| `produtos` | `sku` (chave), `nome`, `quantidade_total` (`CHECK >= 0`) |
| `retencoes` | reservas e bloqueios: `id`, `sku`, `tipo`, `quantidade`, `solicitacao_id`, `motivo`, `status` (`ATIVA`/`LIBERADA`/`EFETIVADA`), datas |
| `movimentacoes` | histórico de cada operação (auditoria) |

### Testes

**122 testes, cobertura de 99%**, cerca de 10 segundos:

| Tipo | Onde | O que garante |
|---|---|---|
| Unitários | `tests/unitarios/` | Regras puras: validações (inclusive `True`, `3.0`, `"3"`), disponível, liberação e efetivação únicas |
| Integração | `tests/integracao/test_servico_estoque.py` | Regras do case com banco real; falha no meio da operação não deixa rastro (rollback); persistência |
| API | `tests/api/` | Status HTTP, contrato de erro, validação, fluxo completo |
| Concorrência | `tests/integracao/test_concorrencia.py` | 50 reservas simultâneas com 10 unidades: exatamente 10 passam; liberar e efetivar a mesma reserva ao mesmo tempo: só uma vence |
| Propriedades | `tests/integracao/test_propriedades.py` | O Hypothesis gera sequências aleatórias de operações e compara o sistema com um modelo de referência a cada passo |

Os testes de concorrência foram validados por mutação: sem as transações no `db.py`, 50 de 50
reservas simultâneas foram aceitas com só 10 unidades, e os testes falharam.

---

## 3. Principais decisões técnicas

Detalhes, alternativas e consequências em [`docs/DECISOES.md`](docs/DECISOES.md).

1. **Disponível calculado, não armazenado**: a soma `total = disponível + reservado +
   bloqueado` vale por construção.
2. **Reserva e bloqueio como um único modelo (`Retencao`)**, com `tipo`: mesmos dados e
   mesmo comportamento.
3. **Liberar e efetivar não apagam**: a retenção muda de status, preservando o histórico e
   diferenciando "não existe" (404) de "já encerrada" (409).
4. **Efetivação atômica da reserva**: a entrega do pedido baixa o total e encerra a reserva na
   mesma transação, sem a brecha de "liberar e depois dar saída".
5. **Uma transação por operação, com `BEGIN IMMEDIATE`**: as escritas são serializadas, e
   duas operações simultâneas nunca leem o mesmo saldo.
6. **SQLite com SQL escrito à mão**: transações ACID sem instalação extra e consultas
   explícitas.
7. **FastAPI**: validação de entrada e Swagger gerados a partir do código.
8. **Domínio isolado e contrato único de erro** (`{codigo, mensagem}`), inclusive para 404 e
   405 de rota.
9. **Trilha de auditoria** gravada na mesma transação da operação.
10. **Validação em duas camadas**: Pydantic no contrato HTTP e o domínio, que se protege
    venha a chamada de onde vier.

---

## 4. Premissas

- Quantidades são **unidades inteiras**, de 1 a 1.000.000 por operação.
- **Total** = unidades fisicamente no estoque. A saída avulsa só consome o disponível;
  unidades reservadas saem pela **efetivação** da reserva.
- Toda reserva e todo bloqueio pertencem a uma **solicitação** (`solicitacao_id`
  obrigatório); a mesma solicitação pode ter várias retenções.
- Uma retenção é encerrada **uma única vez** (liberada ou efetivada), sempre pela quantidade
  inteira. Só reservas podem ser efetivadas.
- O produto precisa ser **cadastrado** antes de receber entradas.
- O **SKU não diferencia maiúsculas de minúsculas** (é guardado em maiúsculas) e aceita
  letras, números, `.`, `-` e `_`, porque faz parte da URL.
- Um único depósito. Datas em UTC.

## 5. Limitações conhecidas

- **Sem idempotência**: se o cliente reenviar uma entrada por timeout, ela é aplicada duas
  vezes.
- **Sem expiração de reservas**: uma reserva esquecida retém estoque até ser liberada.
- **Sem liberação ou efetivação parcial**.
- **SQLite serializa as escritas** e é um arquivo local: serve para uma instância, não para
  várias instâncias da API em paralelo.
- Sem autenticação, sem paginação no histórico e sem migrations versionadas. Ao atualizar o
  código com mudança de tabelas, é preciso apagar o `estoque.db` local.

---

## 6. Instruções para execução

Pré-requisitos: **Python 3.11+** e Git. Os comandos são para o **Prompt de Comando (CMD)**
do Windows; em Linux/macOS, a ativação do ambiente é `source .venv/bin/activate`.

### Instalar

```bat
git clone https://github.com/Thiscesarr/controle-estoque.git
cd controle-estoque
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

### Subir a API

```bat
uvicorn estoque.main:app --reload
```

- Swagger (documentação interativa): **http://localhost:8000/docs**, com *Try it out* em
  cada rota.
- O banco é o arquivo `estoque.db`, criado na pasta atual. Para outro caminho:
  `set ESTOQUE_DB=C:\caminho\outro.db` antes de subir a API.

### Exemplo pelo terminal (em outro CMD)

```bat
curl -X POST http://localhost:8000/produtos -H "Content-Type: application/json" -d "{\"sku\": \"NB-001\", \"nome\": \"Notebook X\"}"
curl -X POST http://localhost:8000/produtos/NB-001/entradas -H "Content-Type: application/json" -d "{\"quantidade\": 10}"
curl -X POST http://localhost:8000/produtos/NB-001/reservas -H "Content-Type: application/json" -d "{\"quantidade\": 3, \"solicitacao_id\": \"PED-1\"}"
curl http://localhost:8000/produtos/NB-001
```

No CMD, o JSON vai entre aspas duplas e as aspas internas são escritas como `\"`.

### Testes e verificações

```bat
pytest
pytest --cov=estoque --cov-report=term-missing
ruff check .
ruff format --check .
```

---

## 7. Em produção, o que eu faria diferente

| # | Mudança | Por quê |
|---|---|---|
| 1 | **PostgreSQL** com lock na linha do produto (`SELECT ... FOR UPDATE`) | O SQLite não atende várias instâncias da API; o lock por produto permite operações em produtos diferentes ao mesmo tempo |
| 2 | **Idempotency-Key** nos `POST` | Em rede real há reenvios por timeout; a mesma requisição não pode ser aplicada duas vezes |
| 3 | **Expiração automática de reservas** | Reserva esquecida prende estoque que poderia ser vendido |
| 4 | **Autenticação e autorização** (OAuth2/JWT), com o usuário gravado em cada movimentação | Saber quem fez cada operação é obrigatório em auditoria |
| 5 | **Observabilidade**: logs estruturados, métricas (latência, taxa de 409) e tracing | Sem isso, investigar uma divergência de estoque é adivinhação |
| 6 | **Migrations versionadas** (Alembic) | Evoluir o schema sem apagar dados |
| 7 | **Eventos de estoque** (padrão *outbox*) | Outros sistemas (vendas, logística) precisam saber das mudanças sem consultar a API |
| 8 | **Docker, dependências travadas e testes de carga** no pipeline | Ambiente reproduzível e limite de desempenho conhecido antes de subir |

Com volume muito alto, eu também manteria os contadores de reservado e bloqueado na linha do
produto, atualizados na mesma transação e protegidos por `CHECK`, para a consulta não
precisar somar as retenções.

---

## 8. Uso de IA

Usei o **Claude** (Anthropic), pelo Claude Code. A IA escreveu a maior parte do código; eu
defini os requisitos, revisei as escolhas, testei a API e pedi as mudanças. O documento
completo, com os prompts, está em [`docs/USO_DE_IA.md`](docs/USO_DE_IA.md).

- **Onde mais ajudou:** escrita do código (Python, FastAPI, Pydantic, SQL), testes de
  concorrência e de propriedades com Hypothesis, e levantamento de casos de borda.
- **Sugestões que modifiquei:** reserva entregue em duas chamadas (liberar + saída) virou uma
  efetivação atômica; SKU sensível a maiúsculas passou a ser normalizado; erros 404/405 do
  framework passaram a seguir o contrato da API; comandos de PowerShell passaram para CMD.
- **Erros identificados:** o modo WAL do SQLite, ativado pela IA, deixou a suíte de testes em
  132 s; a medição mostrou a causa, o WAL foi removido e a suíte caiu para 1,5 s. Também
  foram corrigidos um modelo com dado duplicado e um filtro de aviso do pytest com a
  categoria errada.

---

## 9. Visão de engenharia: 5 aspectos mais importantes

1. **Consistência transacional.** Cada operação acontece por inteiro (estoque + histórico)
   ou não acontece. Em estoque, estado parcial vira prejuízo.
2. **Controle de concorrência.** Pedidos chegam ao mesmo tempo; sem lock, duas operações
   leem o mesmo saldo e vendem a mesma unidade.
3. **Testes automatizados no CI**, focados em regras, concorrência e invariantes: permitem
   mudar o código com segurança.
4. **Rastreabilidade e observabilidade.** O histórico explica por que o estoque está assim;
   logs e métricas mostram o que está acontecendo agora.
5. **Baixo acoplamento e contratos estáveis.** Domínio isolado e contrato de erro único
   permitem evoluir sem quebrar quem usa a API.

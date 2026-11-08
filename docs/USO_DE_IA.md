# Uso de IA

**Ferramenta:** Claude (Anthropic), pelo Claude Code, com acesso à pasta do projeto para criar
arquivos, rodar testes e fazer commits.

**Divisão de papéis:** a IA escreveu a maior parte do código e cuidou da sintaxe. Eu defini os
requisitos e a forma de trabalho, revisei as escolhas técnicas, testei a API e pedi as
mudanças descritas abaixo.

---

## Principais prompts

Resumidos e com a escrita corrigida; o conteúdo é o das mensagens originais.

**1. Planejamento**
> "Segue o case em anexo. Quero fazer em Python. Me diga como você pensa em fazer. O mais
> importante: eu preciso explicar por que cada decisão foi tomada, por que X e não Y, onde
> está cada coisa no repositório e o que cada arquivo faz."

**2. Produto, ferramentas e forma de construção**
> "Descreva a ideia geral, as funcionalidades e a interface: vai ser app ou web? Explique o
> que é cada ferramenta e o que ela faz. Construa o projeto arquivo por arquivo, como num
> desenvolvimento normal, e a cada commit me explique o que mudou e o que cada arquivo faz."

**3. Ambiente de execução**
> "No README, os comandos devem ser para CMD e não PowerShell, porque o PowerShell é
> bloqueado no ambiente do banco."

**4. Testes da API**
> "Monte um roteiro para eu testar a API localmente, no terminal e no Swagger, cobrindo
> todas as funcionalidades e a lógica de negócio, como um QA."

**5. Revisão técnica**
> "Quero mudar três escolhas: a entrega de uma reserva deve ser uma operação atômica, os erros
> 404 e 405 devem seguir o formato de erro da API, e o SKU não deve diferenciar maiúsculas
> de minúsculas."

---

## Onde a IA mais ajudou

- **Escrita do código**: Python, FastAPI, Pydantic e SQL. A estrutura em camadas e os
  arquivos saíram rápido, e eu concentrei o tempo em revisar as decisões.
- **Testes difíceis de escrever à mão**: concorrência com threads disparando ao mesmo
  tempo e testes de propriedades com Hypothesis, que geram sequências aleatórias de operações
  e comparam o sistema com um modelo de referência.
- **Casos de borda**: `True` aceito como inteiro pelo Python, `"3"` e `3.0` convertidos
  silenciosamente para `3` pelo Pydantic, SKU com `/` quebrando a URL.
- **Documentação**: rascunho do README e do registro de decisões, que revisei.

---

## Sugestões da IA que modifiquei

**1. Entrega de reserva em duas chamadas → efetivação atômica.**
A IA implementou só "liberar" e deixou a entrega do pedido como liberar a reserva e depois
registrar a saída. Entre as duas chamadas, outra requisição pode usar as unidades. É o
tipo de inconsistência que o case pede para evitar. Pedi `POST /reservas/{id}/efetivar`, que
baixa o total e encerra a reserva na mesma transação.

**2. SKU sensível a maiúsculas → SKU normalizado.**
Na versão da IA, `nb-001` e `NB-001` eram produtos diferentes. Em operação real, isso gera
cadastro duplicado e estoque dividido entre dois registros do mesmo produto. O SKU passou a
ser guardado e buscado em maiúsculas.

**3. Erros de rota fora do padrão → contrato único.**
O roteiro de testes da API mostrou que rota inexistente (404) e método errado (405) respondiam
`{"detail": ...}`, enquanto o resto da API responde `{"codigo", "mensagem"}`. Um cliente da
API precisa de um único formato de erro. Foi adicionado um tratador para esses erros.

**4. Comandos de PowerShell → CMD.**
O README veio com comandos de PowerShell, que é bloqueado no ambiente do banco. Os comandos
passaram para CMD e foram executados no `cmd.exe` para confirmar que funcionam.

---

## Erros e limitações identificados nas respostas da IA

**1. Modo WAL do SQLite deixou tudo mais lento.**
A IA ativou o WAL pensando em concorrência. A suíte de testes passou a levar 132 s. Medindo,
o custo estava na gravação forçada em disco: com uma conexão por operação, o WAL grava a
cada fechamento de conexão. O WAL foi removido e a suíte caiu para 1,5 s.

**2. Dado duplicado no modelo.**
Na primeira versão, o total ficava tanto em `Produto` quanto em `SaldoEstoque`. Isso
contradizia a própria decisão de não guardar dado duplicado. `Produto` ficou só com os
dados de cadastro.

**3. Filtro de aviso do pytest que não funcionava.**
Para silenciar um aviso de depreciação do Starlette, a IA usou a categoria
`DeprecationWarning`. Não funcionou: no código da biblioteca, a classe herda de
`UserWarning`. Corrigido.

**4. Más práticas na primeira versão dos testes.**
Importação de constantes do `conftest.py` e acesso a atributo privado no teste de
persistência. Corrigido antes do commit.

**5. Leitura do enunciado.**
A IA não conseguiu abrir o PDF diretamente e precisou extrair o texto com `pypdf`; a
primeira tentativa falhou por codificação do terminal do Windows.

---

## Como validei o que a IA produziu

- **122 testes** automatizados, rodando no CI em Python 3.11 e 3.13.
- **Teste de mutação**: as transações foram removidas de propósito e os testes de
  concorrência falharam (50 de 50 reservas aceitas com 10 unidades), confirmando que eles
  detectam o problema.
- **Roteiro de testes manual** da API pelo Swagger e pelo terminal, caso a caso, com o saldo
  esperado em cada passo.
- **Medição** antes de mudar algo por desempenho (caso do WAL).

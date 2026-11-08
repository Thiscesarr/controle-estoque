# Uso de IA

**Ferramenta:** Claude (Anthropic), pelo Claude Code, com acesso à pasta do projeto para criar
arquivos, rodar testes e fazer commits.

**Divisão de papéis:** a IA escreveu a maior parte do código e cuidou da sintaxe. Eu defini os
requisitos e a forma de trabalho, revisei as escolhas técnicas, testei a API e pedi as
mudanças descritas abaixo.

---

## Principais prompts

Com a escrita revisada; o conteúdo é o das mensagens originais.

**1. Entendimento do case e plano de solução**
> "Segue em anexo o case de controle de estoque. Das linguagens permitidas, vou usar Python.
> Antes de escrever qualquer código, quero um plano completo: como você modelaria o problema
> (em especial as quantidades total, disponível, reservada e bloqueada, e as várias reservas e
> bloqueios simultâneos por produto), qual arquitetura, banco de dados, forma de exposição e
> estratégia de testes. Como referência do que eu já domino, considere um projeto pessoal
> anterior: um app Android em Kotlin para controle de despensa, com regras de domínio
> isoladas e testadas e persistência com Room. Ele serve só como referência, não como base.
> Para cada decisão, quero a justificativa: por que essa opção, qual a alternativa descartada
> e por quê, qual o custo da escolha, onde ela fica no repositório e qual a responsabilidade
> de cada arquivo."

**2. Definição da solução e do processo de construção**
> "Antes de implementar, detalhe a solução: a forma de exposição (aplicativo, site ou API) e o
> motivo da escolha, as funcionalidades, os endpoints e a interface de uso. Para cada
> ferramenta da stack, explique o que ela é e qual papel cumpre no projeto. A construção deve
> ser incremental, um commit por etapa, como num desenvolvimento normal: a cada etapa, descreva
> o que mudou, a responsabilidade de cada arquivo novo ou alterado e as decisões tomadas, para
> que eu revise antes de avançar."

**3. Revisão técnica da solução**
> "Revisando o código e testando a API, quero mudar três decisões. Primeiro: a entrega de um
> pedido reservado hoje exige liberar a reserva e depois registrar a saída, em duas chamadas;
> nesse intervalo outra requisição pode usar as unidades. Quero uma operação de efetivação que
> baixe o total e encerre a reserva na mesma transação, com um teste de concorrência em que
> liberar e efetivar disputam a mesma reserva. Segundo: rota inexistente e método não
> permitido respondem no formato padrão do FastAPI; todo erro da API deve seguir o contrato
> `{codigo, mensagem}`. Terceiro: o SKU não deve diferenciar maiúsculas de minúsculas, para
> que o mesmo produto não possa ser cadastrado duas vezes."

**4. Segunda revisão: falhas e lacunas**
> "Analisei a solução e encontrei pontos que podem ser questionados. Travei o banco de
> propósito e fiz uma entrada: a resposta veio `500 text/plain`, fora do contrato que o README
> promete; banco ocupado deve responder 503 com `Retry-After`, e qualquer outro erro, 500 no
> formato `{codigo, mensagem}`, com log. O bloqueio não tem baixa definitiva: uma caixa
> avariada que vai para o lixo exige liberar e depois dar saída, a mesma brecha de duas
> chamadas que foi fechada na reserva. E não há como listar as reservas e bloqueios de um
> produto ou de uma solicitação; se o cliente perder o id da reserva do PED-1, não consegue
> liberá-la. Também quero travar as versões das dependências, porque o Starlette já emite aviso
> de depreciação, e documentar que uma unidade reservada não pode virar bloqueio diretamente."

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

**1. Saída de unidades retidas em duas chamadas → operação atômica.**
A IA implementou só "liberar" e deixou a entrega do pedido como liberar a reserva e depois
registrar a saída. Entre as duas chamadas, outra requisição pode usar as unidades. É o
tipo de inconsistência que o case pede para evitar. Pedi `POST /reservas/{id}/efetivar`, que
baixa o total e encerra a reserva na mesma transação. Na segunda revisão, apliquei a mesma
correção ao bloqueio: `POST /bloqueios/{id}/baixar`, para a perda por avaria.

**2. SKU sensível a maiúsculas → SKU normalizado.**
Na versão da IA, `nb-001` e `NB-001` eram produtos diferentes. Em operação real, isso gera
cadastro duplicado e estoque dividido entre dois registros do mesmo produto. O SKU passou a
ser guardado e buscado em maiúsculas.

**3. Erros fora do padrão → contrato único.**
Nos testes da API, rota inexistente (404) e método errado (405) respondiam `{"detail": ...}`,
e banco travado ou erro inesperado respondiam `500` em texto puro, enquanto o resto da API
responde `{"codigo", "mensagem"}`. Um cliente da API precisa de um único formato de erro e de
saber quando vale repetir. Hoje: 404/405 no contrato, banco ocupado → 503 com `Retry-After`,
erro inesperado → 500 `ERRO_INTERNO` com log.

**4. Retenções sem listagem → consulta por produto e solicitação.**
Só era possível consultar uma reserva pelo id. Se o cliente perdesse o id, não teria como
liberá-la. Foi incluído `GET /produtos/{sku}/retencoes`, com filtros por tipo, status e
solicitação.

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

- **131 testes** automatizados, rodando no CI em Python 3.11 e 3.13.
- **Teste de mutação**: as transações foram removidas de propósito e os testes de
  concorrência falharam (50 de 50 reservas aceitas com 10 unidades), confirmando que eles
  detectam o problema.
- **Testes manuais** da API pelo Swagger e pelo terminal, conferindo o saldo esperado depois
  de cada operação.
- **Concorrência via HTTP** com `scripts/estresse.py`: 300 requisições simultâneas contra a API
  no ar, saldo final conferido e nenhum erro.
- **Medição** antes de mudar algo por desempenho (caso do WAL).

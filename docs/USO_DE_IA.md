# Uso de IA no desenvolvimento

**Ferramenta:** Claude (Anthropic), pelo Claude Code no aplicativo desktop. A IA tinha acesso
à pasta do projeto para criar arquivos, rodar comandos, executar os testes e fazer commits.

**Divisão de papéis:** a maior parte do código foi escrita pela IA. Meu papel foi definir o
objetivo, a linguagem e a forma de trabalho, acompanhar as etapas com perguntas de
checagem e revisar o código e os resumos de cada commit, para conseguir explicar e
defender cada decisão.

> Este documento foi redigido com apoio da IA a partir do histórico real da conversa e
> revisado por mim. Os prompts abaixo estão resumidos e com a escrita corrigida; o conteúdo
> é o das mensagens originais.

---

## Como usei a IA

1. **Entendimento e plano.** Enviei o PDF do case e pedi um plano em Python, com a
   justificativa de cada decisão. Indiquei um projeto pessoal anterior (um app Android de
   controle de despensa) apenas como referência do que eu já conhecia.
2. **Revisão do plano e da forma de trabalho.** Pedi mais clareza sobre o produto (seria app
   ou web?) e sobre cada ferramenta, e defini que o projeto seria construído de forma
   incremental, commit a commit, com um resumo completo de cada alteração.
3. **Etapas 0 a 2** (estrutura do projeto, modelo de domínio, erros e validações):
   construídas uma de cada vez. A cada etapa, a IA explicava o que mudou e por quê e fazia
   perguntas de checagem, que eu respondia antes de seguir.
4. **Etapas seguintes** (testes, banco, repositório, serviço, API, concorrência, CI e
   documentação): por causa do prazo, pedi que a IA implementasse o restante seguindo as
   mesmas práticas e me entregasse um resumo detalhado de cada commit, que usei como roteiro
   de revisão e estudo.

## Principais prompts

**1. Planejamento**
> "Vou te enviar o case em anexo. Me diga como você pensa em fazer. Das linguagens
> permitidas, quero Python. Pode usar o repositório do meu app de despensa como referência,
> só para verificar o que eu já tenho de conhecimento; é uma proposta diferente. O mais
> importante: eu preciso conseguir explicar **por que** cada decisão foi tomada, **como**
> foi tomada, por que X e não Y, onde está cada coisa no repositório e o que cada arquivo
> faz."

**2. Ajuste do plano e do processo**
> "Ajuste o plano: descreva a ideia geral, as funcionalidades e a interface. Vai ser um app
> ou web? Explique o que são as ferramentas, além de quais vão ser. Não quero o projeto
> completo de uma vez: quero que os arquivos sejam feitos como num desenvolvimento humano,
> passo a passo, para eu acompanhar lendo os arquivos. A cada commit, me faça um resumo
> completo do que foi mudado e do que cada arquivo faz."

**3. Checagem de entendimento** (respostas às perguntas da IA, resumidas)
> "O ambiente virtual tem dados exclusivos do meu PC; quem clonar recria com um comando.
> Com o código em `src/`, os testes são obrigados a usar o pacote instalado, como em
> produção. O disponível não é guardado porque um número derivado guardado pode ficar
> errado; calculado, ele está sempre certo. Reserva e bloqueio são a mesma classe porque têm
> os mesmos dados e comportamento; eu separaria se um deles precisasse de dados ou regras
> próprios. O bloqueio também tem solicitação porque ele também nasce de uma solicitação,
> como um pedido de inspeção ou verificação do estoque."

**4. Implementação do restante**
> "Pode seguir com a implementação completa, usando boas práticas, publicando no GitHub, e
> me envie os resumos de cada alteração. Depois eu estudo as funcionalidades, testo e leio
> o código."

## Onde a IA mais ajudou

- **Modelagem:** transformar o enunciado em um modelo sem redundância (disponível
  calculado; reserva e bloqueio como um único conceito de retenção).
- **Casos de borda** que eu não teria listado sozinho: `True` aceito como inteiro pelo Python,
  SKU com `/` quebrando a URL, `3.0` e `"3"` convertidos silenciosamente para `3`, campos
  com erro de digitação, liberar uma reserva pela rota de bloqueios.
- **Testes de concorrência e baseados em propriedades** (Hypothesis), técnicas que eu ainda
  não dominava.
- **Diagnóstico de desempenho** com medição em vez de suposição (ver erros abaixo).
- **Explicações didáticas** de cada commit, que viraram meu material de estudo.

## Sugestões da IA que modifiquei

1. **Conteúdo do plano.** A primeira resposta foi direto para arquitetura e stack, sem deixar
   claro o que seria o produto (app? site?) nem o que cada ferramenta faz. Pedi que
   reescrevesse o plano a partir do produto, explicando cada ferramenta, e tornei regra que
   nada seria entregue de uma vez (construção incremental, com resumo completo por commit).
   Motivo: preciso entender o "o quê" antes do "como" para conseguir apresentar e defender.
2. **Ritmo das etapas.** A IA propôs parar a cada etapa até eu responder às perguntas de
   checagem. Depois de três etapas, mudei para implementação contínua do restante, com
   resumos detalhados por commit. Motivo: prazo de entrega. Custo assumido: a revisão
   dessas etapas foi feita depois, a partir dos resumos e do código.

## Erros, limitações e problemas identificados nas respostas da IA

1. **Leitura do enunciado.** A IA não conseguiu abrir o PDF diretamente (faltava uma
   ferramenta de renderização no ambiente) e extraiu o texto com a biblioteca `pypdf`. A
   primeira tentativa falhou por um problema de codificação do terminal do Windows. Lição:
   conferir que a IA leu o enunciado completo antes de confiar no plano.
2. **Ordem do plano.** O plano previa as validações na etapa do modelo, mas elas dependiam dos
   erros que só seriam criados na etapa seguinte. A ordem foi ajustada durante a execução.
3. **Modelo revisado.** Na primeira versão, `Produto` tinha `quantidade_total` e o
   `SaldoEstoque` também. A duplicação apareceu ao escrever o serviço e foi refatorada:
   `Produto` ficou só com dados de cadastro, e as quantidades, no saldo.
4. **Otimização prematura (modo WAL).** A IA ativou o modo WAL do SQLite pensando em
   concorrência. A suíte de testes passou a levar 132 s. A medição mostrou que, com uma
   conexão por operação, o WAL forçava gravação em disco a cada fechamento. O WAL foi
   removido e a suíte caiu para 1,5 s. Lição: medir antes de otimizar.
5. **Filtro de aviso com a categoria errada.** Para silenciar um aviso de depreciação do
   Starlette, a primeira versão do filtro usou `DeprecationWarning` e não funcionou. Lendo o
   código da biblioteca, a classe real herda de `UserWarning`. Corrigido.
6. **Detalhes de boas práticas nos testes.** A primeira versão importava constantes do
   `conftest.py` e acessava um atributo privado no teste de persistência. Corrigido antes do
   commit (módulo `tests/apoio.py` e caminho do banco via `tmp_path`).

## Como validei o que a IA produziu

- **Testes automatizados** em cinco níveis, executados a cada mudança e no CI (Python 3.11 e
  3.13).
- **Testes de mutação manuais:** removemos de propósito as transações do código e
  confirmamos que os testes de concorrência falham (50 de 50 reservas aceitas com só 10
  unidades). Um teste que nunca falha não prova nada.
- **Medição** antes de decisões de desempenho (caso do WAL).
- **Leitura do código-fonte das bibliotecas** quando o comportamento não batia com o
  esperado (caso do aviso do Starlette).
- **Execução real do servidor** (`uvicorn`) e chamadas HTTP, além dos testes.

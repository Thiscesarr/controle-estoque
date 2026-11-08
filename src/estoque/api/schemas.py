"""Contratos da API: o formato do JSON que entra e que sai.

O Pydantic valida o JSON recebido ANTES de o código da rota rodar. Se algo estiver fora do
contrato (campo faltando, texto no lugar de número...), a API responde 422 sozinha.

Os schemas são separados dos modelos do domínio de propósito: o contrato público da API
pode mudar (versões, nomes de campos) sem obrigar o domínio a mudar, e vice-versa.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from estoque.domain.models import (
    PADRAO_SKU,
    QUANTIDADE_MAXIMA_POR_OPERACAO,
    TAMANHO_MAXIMO_TEXTO,
    Movimentacao,
    Retencao,
    SituacaoProduto,
    StatusRetencao,
    TipoMovimentacao,
    TipoRetencao,
)

# strict=True: aceita só números inteiros de verdade. Sem isso, o Pydantic converteria
# "3" (texto) e 3.0 (decimal) para 3 silenciosamente.
Quantidade = Annotated[
    int,
    Field(strict=True, ge=1, le=QUANTIDADE_MAXIMA_POR_OPERACAO, examples=[10]),
]


class Requisicao(BaseModel):
    """Base dos corpos de requisição: campos desconhecidos (ex.: "quantidae") geram 422."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- requisições


class ProdutoCriar(Requisicao):
    sku: str = Field(pattern=PADRAO_SKU, examples=["NB-001"])
    nome: str = Field(min_length=1, max_length=TAMANHO_MAXIMO_TEXTO, examples=["Notebook X"])


class Movimento(Requisicao):
    """Corpo das operações de entrada e saída."""

    quantidade: Quantidade


class RetencaoCriar(Requisicao):
    """Corpo das operações de reservar e bloquear."""

    quantidade: Quantidade
    solicitacao_id: str = Field(
        min_length=1,
        max_length=TAMANHO_MAXIMO_TEXTO,
        description="Solicitação/pedido que originou a reserva ou o bloqueio.",
        examples=["PED-1"],
    )
    motivo: str | None = Field(
        default=None,
        max_length=TAMANHO_MAXIMO_TEXTO,
        description="Texto livre opcional.",
        examples=["Cliente aguardando pagamento"],
    )


# ------------------------------------------------------------------ respostas


class SituacaoResposta(BaseModel):
    sku: str
    nome: str
    quantidade_total: int
    quantidade_disponivel: int
    quantidade_reservada: int
    quantidade_bloqueada: int

    @classmethod
    def de_dominio(cls, situacao: SituacaoProduto) -> "SituacaoResposta":
        saldo = situacao.saldo
        return cls(
            sku=situacao.produto.sku,
            nome=situacao.produto.nome,
            quantidade_total=saldo.quantidade_total,
            quantidade_disponivel=saldo.quantidade_disponivel,
            quantidade_reservada=saldo.quantidade_reservada,
            quantidade_bloqueada=saldo.quantidade_bloqueada,
        )


class RetencaoResposta(BaseModel):
    id: str
    sku: str
    tipo: TipoRetencao
    quantidade: int
    solicitacao_id: str
    motivo: str | None
    status: StatusRetencao
    criada_em: datetime
    liberada_em: datetime | None
    efetivada_em: datetime | None

    @classmethod
    def de_dominio(cls, retencao: Retencao) -> "RetencaoResposta":
        return cls(
            id=retencao.id,
            sku=retencao.sku,
            tipo=retencao.tipo,
            quantidade=retencao.quantidade,
            solicitacao_id=retencao.solicitacao_id,
            motivo=retencao.motivo,
            status=retencao.status,
            criada_em=retencao.criada_em,
            liberada_em=retencao.liberada_em,
            efetivada_em=retencao.efetivada_em,
        )


class MovimentacaoResposta(BaseModel):
    id: int
    tipo: TipoMovimentacao
    quantidade: int
    retencao_id: str | None
    ocorrida_em: datetime

    @classmethod
    def de_dominio(cls, movimentacao: Movimentacao) -> "MovimentacaoResposta":
        return cls(
            id=movimentacao.id,
            tipo=movimentacao.tipo,
            quantidade=movimentacao.quantidade,
            retencao_id=movimentacao.retencao_id,
            ocorrida_em=movimentacao.ocorrida_em,
        )


class ErroResposta(BaseModel):
    """Formato único de erro da API: o cliente sempre sabe onde ler o que deu errado."""

    codigo: str = Field(examples=["QUANTIDADE_INDISPONIVEL"])
    mensagem: str = Field(examples=["Quantidade solicitada (6) maior que a disponível (5)"])

"""Modelos do domínio de estoque.

Este módulo descreve O QUE existe no negócio (o produto, o saldo, as retenções e o
histórico de movimentações) e as regras que dependem só desses dados (ex.: "não reservar
mais do que o disponível"). Ele não sabe nada de banco de dados nem de HTTP.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from estoque.domain.errors import (
    DadoInvalido,
    QuantidadeIndisponivel,
    QuantidadeInvalida,
    RetencaoJaEfetivada,
    RetencaoJaLiberada,
)

QUANTIDADE_MAXIMA_POR_OPERACAO = 1_000_000
"""Limite de unidades em uma única operação.

Protege contra erros de digitação (ex.: 1000000 no lugar de 100) e mantém os totais longe
do limite de inteiros de 64 bits do banco de dados.
"""

PADRAO_SKU = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$"
"""SKU: de 1 a 50 caracteres, começando por letra ou número; aceita '.', '-' e '_'.

O SKU faz parte da URL (/produtos/{sku}), por isso espaços e barras não são aceitos.
"""

TAMANHO_MAXIMO_TEXTO = 200


def validar_quantidade(quantidade: object) -> int:
    """Garante que a quantidade de uma operação é um inteiro entre 1 e o limite.

    `bool` é rejeitado de propósito: em Python, `True` é um `int` que vale 1, e aceitar
    `True` como "1 unidade" esconderia um erro de quem chamou.
    """
    if (
        isinstance(quantidade, bool)
        or not isinstance(quantidade, int)
        or not 1 <= quantidade <= QUANTIDADE_MAXIMA_POR_OPERACAO
    ):
        raise QuantidadeInvalida(quantidade, QUANTIDADE_MAXIMA_POR_OPERACAO)
    return quantidade


def normalizar_sku(sku: str) -> str:
    """SKU não diferencia maiúsculas de minúsculas: `nb-001` e `NB-001` são o mesmo produto.

    O SKU é guardado e buscado sempre em maiúsculas. Só textos ASCII são convertidos, para
    que um caractere especial nunca "vire" outro SKU válido ao passar por `upper()`.
    """
    return sku.upper() if sku.isascii() else sku


def validar_sku(sku: object) -> str:
    """Garante que o SKU segue o formato aceito (ver `PADRAO_SKU`) e o devolve normalizado."""
    if not isinstance(sku, str) or re.fullmatch(PADRAO_SKU, sku) is None:
        raise DadoInvalido("sku", "use de 1 a 50 caracteres entre letras, números, '.', '-' e '_'")
    return normalizar_sku(sku)


def validar_texto(valor: object, campo: str) -> str:
    """Texto obrigatório: remove espaços das pontas, não pode ficar vazio nem ser longo demais."""
    if not isinstance(valor, str):
        raise DadoInvalido(campo, "deve ser um texto")
    texto = valor.strip()
    if not texto:
        raise DadoInvalido(campo, "não pode ser vazio")
    if len(texto) > TAMANHO_MAXIMO_TEXTO:
        raise DadoInvalido(campo, f"deve ter no máximo {TAMANHO_MAXIMO_TEXTO} caracteres")
    return texto


class TipoRetencao(StrEnum):
    """Motivo pelo qual uma quantidade está fora do disponível."""

    RESERVA = "RESERVA"  # separada para atender uma solicitação/pedido
    BLOQUEIO = "BLOQUEIO"  # indisponível para uso (avaria, inspeção de qualidade...)


class StatusRetencao(StrEnum):
    """Ciclo de vida de uma retenção: nasce ativa e é encerrada uma única vez.

    LIBERADA: as unidades voltaram ao disponível.
    EFETIVADA: as unidades saíram do estoque (reserva entregue ou bloqueio baixado por perda).
    """

    ATIVA = "ATIVA"
    LIBERADA = "LIBERADA"
    EFETIVADA = "EFETIVADA"


class TipoMovimentacao(StrEnum):
    """Tudo o que pode acontecer com o estoque de um produto (trilha de auditoria)."""

    ENTRADA = "ENTRADA"
    SAIDA = "SAIDA"
    RESERVA = "RESERVA"
    BLOQUEIO = "BLOQUEIO"
    LIBERACAO_RESERVA = "LIBERACAO_RESERVA"
    LIBERACAO_BLOQUEIO = "LIBERACAO_BLOQUEIO"
    EFETIVACAO_RESERVA = "EFETIVACAO_RESERVA"
    BAIXA_BLOQUEIO = "BAIXA_BLOQUEIO"


@dataclass(frozen=True)
class Produto:
    """Dados de cadastro de um produto: o que ele é (as quantidades ficam no saldo)."""

    sku: str
    nome: str


@dataclass(frozen=True)
class SaldoEstoque:
    """Retrato das quantidades de um produto em um determinado momento.

    Só o total é guardado no banco. O reservado e o bloqueado são somados a partir das
    retenções ativas, e o disponível é calculado aqui. Assim não existe um número
    guardado que possa divergir dos demais.
    """

    quantidade_total: int
    quantidade_reservada: int
    quantidade_bloqueada: int

    @property
    def quantidade_disponivel(self) -> int:
        """O disponível é sempre derivado: total menos tudo que está retido."""
        return self.quantidade_total - self.quantidade_reservada - self.quantidade_bloqueada

    def garantir_disponivel(self, quantidade: int) -> None:
        """Regra comum a saída, reserva e bloqueio: só se usa o que está disponível."""
        if quantidade > self.quantidade_disponivel:
            raise QuantidadeIndisponivel(quantidade, self.quantidade_disponivel)


@dataclass(frozen=True)
class SituacaoProduto:
    """Resultado da operação Consultar: o produto e o seu saldo, lidos no mesmo instante."""

    produto: Produto
    saldo: SaldoEstoque


@dataclass(frozen=True)
class Retencao:
    """Uma quantidade de um produto retida por uma reserva ou por um bloqueio.

    Reserva e bloqueio têm exatamente os mesmos dados e o mesmo comportamento (consomem o
    disponível e, ao serem liberados, devolvem a quantidade a ele). Por isso são um único
    modelo, diferenciados pelo campo `tipo`.
    """

    id: str  # identificação usada para consultar e liberar a retenção
    sku: str  # produto ao qual a retenção pertence
    tipo: TipoRetencao
    quantidade: int
    solicitacao_id: str  # solicitação/pedido que originou a retenção (pode se repetir)
    status: StatusRetencao
    criada_em: datetime
    motivo: str | None = None  # texto livre opcional (ex.: "caixa avariada")
    liberada_em: datetime | None = None
    efetivada_em: datetime | None = None

    @property
    def ativa(self) -> bool:
        """Só retenções ativas contam como quantidade reservada/bloqueada."""
        return self.status == StatusRetencao.ATIVA

    def liberar(self, em: datetime) -> "Retencao":
        """Devolve uma NOVA retenção, já liberada no instante `em`.

        O objeto original não muda (é imutável); quem chama decide o que fazer com o
        resultado (gravar no banco). O instante é recebido como parâmetro, em vez de ler o
        relógio aqui dentro, para que a regra seja previsível e fácil de testar.
        """
        self._garantir_ativa()
        return replace(self, status=StatusRetencao.LIBERADA, liberada_em=em)

    def efetivar(self, em: datetime) -> "Retencao":
        """Devolve uma NOVA retenção, efetivada no instante `em`: as unidades saíram do estoque.

        Para uma reserva, é a entrega do pedido; para um bloqueio, a baixa por perda.
        """
        self._garantir_ativa()
        return replace(self, status=StatusRetencao.EFETIVADA, efetivada_em=em)

    def _garantir_ativa(self) -> None:
        """Uma retenção só muda de estado uma vez: liberada ou efetivada, não volta atrás."""
        if self.status == StatusRetencao.LIBERADA:
            raise RetencaoJaLiberada(self.id)
        if self.status == StatusRetencao.EFETIVADA:
            raise RetencaoJaEfetivada(self.id)


@dataclass(frozen=True)
class Movimentacao:
    """Registro imutável de algo que aconteceu com o estoque de um produto."""

    id: int
    sku: str
    tipo: TipoMovimentacao
    quantidade: int
    ocorrida_em: datetime
    retencao_id: str | None = None  # preenchido em reservas, bloqueios e liberações

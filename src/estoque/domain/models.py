"""Modelos do domínio de estoque.

Este módulo descreve O QUE existe no negócio (o produto, as retenções e o saldo) e as
regras que dependem só desses dados (ex.: "não reservar mais do que o disponível").
Ele não sabe nada de banco de dados nem de HTTP.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from estoque.domain.errors import QuantidadeIndisponivel, QuantidadeInvalida, RetencaoJaLiberada


def validar_quantidade(quantidade: object) -> int:
    """Garante que a quantidade de uma operação é um inteiro maior que zero.

    `bool` é rejeitado de propósito: em Python, `True` é um `int` que vale 1, e aceitar
    `True` como "1 unidade" esconderia um erro de quem chamou.
    """
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or quantidade <= 0:
        raise QuantidadeInvalida(quantidade)
    return quantidade


class TipoRetencao(StrEnum):
    """Motivo pelo qual uma quantidade está fora do disponível."""

    RESERVA = "RESERVA"  # separada para atender uma solicitação/pedido
    BLOQUEIO = "BLOQUEIO"  # indisponível para uso (avaria, inspeção de qualidade...)


class StatusRetencao(StrEnum):
    """Ciclo de vida de uma retenção: nasce ativa e pode ser liberada uma única vez."""

    ATIVA = "ATIVA"
    LIBERADA = "LIBERADA"


@dataclass(frozen=True)
class Produto:
    """Um produto do estoque.

    Guardamos apenas a quantidade total (unidades fisicamente no estoque). As quantidades
    reservada, bloqueada e disponível são calculadas a partir das retenções ativas, para
    que não exista um número guardado que possa divergir dos demais.
    """

    sku: str
    nome: str
    quantidade_total: int = 0


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
    solicitacao_id: str  # solicitação/pedido que originou a retenção
    status: StatusRetencao
    criada_em: datetime
    motivo: str | None = None  # texto livre opcional (ex.: "caixa avariada")
    liberada_em: datetime | None = None

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
        if not self.ativa:
            raise RetencaoJaLiberada(self.id)
        return replace(self, status=StatusRetencao.LIBERADA, liberada_em=em)


@dataclass(frozen=True)
class SaldoEstoque:
    """Retrato das quantidades de um produto em um determinado momento."""

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

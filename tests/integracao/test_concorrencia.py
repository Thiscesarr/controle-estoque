"""Testes de concorrência: várias operações ao mesmo tempo, em threads diferentes.

Simulam vários pedidos chegando juntos. Cada thread abre a própria conexão com o banco,
exatamente como acontece com requisições simultâneas na API.

Sem a transação `BEGIN IMMEDIATE`, duas threads poderiam ler "1 disponível" ao mesmo tempo
e as duas reservariam, vendendo mais do que existe (o problema clássico de "overselling").
"""

import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from estoque.domain.errors import QuantidadeIndisponivel, RetencaoJaLiberada
from estoque.service import ServicoEstoque

T = TypeVar("T")


def executar_ao_mesmo_tempo(acoes: list[Callable[[], T]]) -> list[T]:
    """Roda cada ação em uma thread; a barreira faz todas começarem no mesmo instante."""
    barreira = threading.Barrier(len(acoes))

    def executar(acao: Callable[[], T]) -> T:
        barreira.wait()
        return acao()

    with ThreadPoolExecutor(max_workers=len(acoes)) as executor:
        return list(executor.map(executar, acoes))


def test_reservas_simultaneas_nunca_ultrapassam_o_disponivel(
    servico: ServicoEstoque, sku_com_estoque: str
) -> None:
    """10 unidades e 50 pedidos simultâneos de 1 unidade: exatamente 10 conseguem."""

    def tentar_reservar(numero: int) -> Callable[[], bool]:
        def acao() -> bool:
            try:
                servico.reservar(sku_com_estoque, 1, f"PED-{numero}")
                return True
            except QuantidadeIndisponivel:
                return False

        return acao

    resultados = executar_ao_mesmo_tempo([tentar_reservar(n) for n in range(50)])

    assert sum(resultados) == 10
    saldo = servico.consultar(sku_com_estoque).saldo
    assert saldo.quantidade_reservada == 10
    assert saldo.quantidade_disponivel == 0


def test_operacoes_mistas_simultaneas_mantem_o_estoque_consistente(
    servico: ServicoEstoque, sku_com_estoque: str
) -> None:
    """Saídas, reservas e bloqueios disputando as mesmas 10 unidades ao mesmo tempo."""
    sorteio = random.Random(42)  # semente fixa: o teste é sempre o mesmo
    planos = [
        (sorteio.choice(["saida", "reserva", "bloqueio"]), sorteio.randint(1, 3)) for _ in range(30)
    ]

    def tentar(operacao: str, quantidade: int, numero: int) -> Callable[[], bool]:
        def acao() -> bool:
            try:
                if operacao == "saida":
                    servico.registrar_saida(sku_com_estoque, quantidade)
                elif operacao == "reserva":
                    servico.reservar(sku_com_estoque, quantidade, f"S-{numero}")
                else:
                    servico.bloquear(sku_com_estoque, quantidade, f"S-{numero}")
                return True
            except QuantidadeIndisponivel:
                return False

        return acao

    resultados = executar_ao_mesmo_tempo(
        [tentar(operacao, qtd, n) for n, (operacao, qtd) in enumerate(planos)]
    )
    bem_sucedidas = [plano for plano, ok in zip(planos, resultados, strict=True) if ok]

    def soma(operacao: str) -> int:
        return sum(qtd for op, qtd in bem_sucedidas if op == operacao)

    saldo = servico.consultar(sku_com_estoque).saldo
    assert saldo.quantidade_total == 10 - soma("saida")
    assert saldo.quantidade_reservada == soma("reserva")
    assert saldo.quantidade_bloqueada == soma("bloqueio")
    assert saldo.quantidade_disponivel >= 0
    # Cada operação bem-sucedida deixou exatamente um registro no histórico (+1 da entrada).
    assert len(servico.listar_movimentacoes(sku_com_estoque)) == 1 + len(bem_sucedidas)


def test_liberacoes_simultaneas_da_mesma_reserva_devolvem_uma_unica_vez(
    servico: ServicoEstoque, sku_com_estoque: str
) -> None:
    reserva = servico.reservar(sku_com_estoque, 3, "PED-1")

    def tentar_liberar() -> bool:
        try:
            servico.liberar_reserva(reserva.id)
            return True
        except RetencaoJaLiberada:
            return False

    resultados = executar_ao_mesmo_tempo([tentar_liberar for _ in range(10)])

    assert sum(resultados) == 1
    saldo = servico.consultar(sku_com_estoque).saldo
    assert saldo.quantidade_disponivel == 10  # e não 10 + 3 * 9

"""Testes de integração: serviço + repositório + banco SQLite de verdade.

Aqui validamos as regras do case de ponta a ponta na camada de negócio, incluindo o que
um teste unitário não alcança: transações, rollback e persistência.
"""

from pathlib import Path

import pytest

from estoque.db import BancoDeDados
from estoque.domain.errors import (
    DadoInvalido,
    ProdutoJaCadastrado,
    ProdutoNaoEncontrado,
    QuantidadeIndisponivel,
    QuantidadeInvalida,
    RetencaoJaEfetivada,
    RetencaoJaLiberada,
    RetencaoNaoEncontrada,
)
from estoque.domain.models import SaldoEstoque, StatusRetencao, TipoMovimentacao, TipoRetencao
from estoque.repository import RepositorioEstoque
from estoque.service import ServicoEstoque
from tests.apoio import INSTANTE_FIXO, SKU


def saldo(servico: ServicoEstoque, sku: str = SKU) -> tuple[int, int, int, int]:
    """(total, disponível, reservada, bloqueada), para comparar tudo numa linha só."""
    s: SaldoEstoque = servico.consultar(sku).saldo
    return (
        s.quantidade_total,
        s.quantidade_disponivel,
        s.quantidade_reservada,
        s.quantidade_bloqueada,
    )


class TestCadastroEConsulta:
    def test_produto_novo_comeca_zerado(self, servico: ServicoEstoque) -> None:
        situacao = servico.cadastrar_produto("NB-001", "  Notebook X  ")

        assert situacao.produto.nome == "Notebook X"  # espaços das pontas removidos
        assert saldo(servico) == (0, 0, 0, 0)

    def test_nao_permite_cadastrar_o_mesmo_sku_duas_vezes(self, servico: ServicoEstoque) -> None:
        servico.cadastrar_produto(SKU, "Notebook X")
        with pytest.raises(ProdutoJaCadastrado):
            servico.cadastrar_produto(SKU, "Outro nome")

    def test_rejeita_sku_em_formato_invalido(self, servico: ServicoEstoque) -> None:
        with pytest.raises(DadoInvalido):
            servico.cadastrar_produto("NB 001", "Notebook X")

    def test_sku_nao_diferencia_maiusculas_de_minusculas(self, servico: ServicoEstoque) -> None:
        criado = servico.cadastrar_produto("nb-001", "Notebook X")
        servico.registrar_entrada("Nb-001", 4)

        assert criado.produto.sku == "NB-001"  # guardado em maiúsculas
        assert saldo(servico, "nb-001") == (4, 4, 0, 0)
        with pytest.raises(ProdutoJaCadastrado):
            servico.cadastrar_produto("NB-001", "Duplicado")

    def test_consultar_produto_inexistente(self, servico: ServicoEstoque) -> None:
        with pytest.raises(ProdutoNaoEncontrado):
            servico.consultar("NAO-EXISTE")


class TestEntradaESaida:
    def test_entrada_aumenta_total_e_disponivel(self, servico, sku_com_estoque) -> None:
        servico.registrar_entrada(sku_com_estoque, 5)
        assert saldo(servico) == (15, 15, 0, 0)

    def test_saida_diminui_total_e_disponivel(self, servico, sku_com_estoque) -> None:
        servico.registrar_saida(sku_com_estoque, 4)
        assert saldo(servico) == (6, 6, 0, 0)

    def test_saida_de_todo_o_disponivel_zera_o_estoque(self, servico, sku_com_estoque) -> None:
        servico.registrar_saida(sku_com_estoque, 10)
        assert saldo(servico) == (0, 0, 0, 0)

    def test_saida_maior_que_o_disponivel_e_recusada_sem_alterar_nada(
        self, servico, sku_com_estoque
    ) -> None:
        with pytest.raises(QuantidadeIndisponivel):
            servico.registrar_saida(sku_com_estoque, 11)
        assert saldo(servico) == (10, 10, 0, 0)

    def test_saida_nao_pode_usar_unidades_reservadas_ou_bloqueadas(
        self, servico, sku_com_estoque
    ) -> None:
        servico.reservar(sku_com_estoque, 6, "PED-1")
        servico.bloquear(sku_com_estoque, 2, "INS-1")

        with pytest.raises(QuantidadeIndisponivel) as erro:
            servico.registrar_saida(sku_com_estoque, 3)  # total 10, mas só 2 disponíveis

        assert erro.value.disponivel == 2
        assert saldo(servico) == (10, 2, 6, 2)

    def test_quantidade_invalida_e_recusada_antes_de_tocar_no_banco(
        self, servico, sku_com_estoque
    ) -> None:
        with pytest.raises(QuantidadeInvalida):
            servico.registrar_entrada(sku_com_estoque, 0)

    def test_entrada_em_produto_inexistente(self, servico: ServicoEstoque) -> None:
        with pytest.raises(ProdutoNaoEncontrado):
            servico.registrar_entrada("NAO-EXISTE", 1)


class TestReservaEBloqueio:
    def test_reservar_move_quantidade_do_disponivel_para_reservado(
        self, servico, sku_com_estoque
    ) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")

        assert reserva.id == "ret-1"
        assert reserva.tipo == TipoRetencao.RESERVA
        assert reserva.status == StatusRetencao.ATIVA
        assert reserva.criada_em == INSTANTE_FIXO
        assert saldo(servico) == (10, 7, 3, 0)

    def test_bloquear_move_quantidade_do_disponivel_para_bloqueado(
        self, servico, sku_com_estoque
    ) -> None:
        bloqueio = servico.bloquear(sku_com_estoque, 2, "INS-1", motivo="caixa avariada")

        assert bloqueio.tipo == TipoRetencao.BLOQUEIO
        assert bloqueio.motivo == "caixa avariada"
        assert saldo(servico) == (10, 8, 0, 2)

    def test_varias_reservas_e_bloqueios_de_solicitacoes_diferentes_somam(
        self, servico, sku_com_estoque
    ) -> None:
        servico.reservar(sku_com_estoque, 2, "PED-1")
        servico.reservar(sku_com_estoque, 3, "PED-2")
        servico.reservar(sku_com_estoque, 1, "PED-1")  # mesma solicitação pode reservar de novo
        servico.bloquear(sku_com_estoque, 1, "INS-1")
        servico.bloquear(sku_com_estoque, 2, "INS-2")

        assert saldo(servico) == (10, 1, 6, 3)

    @pytest.mark.parametrize("operacao", ["reservar", "bloquear"])
    def test_nao_retem_mais_do_que_o_disponivel(self, servico, sku_com_estoque, operacao) -> None:
        servico.reservar(sku_com_estoque, 8, "PED-1")

        with pytest.raises(QuantidadeIndisponivel):
            getattr(servico, operacao)(sku_com_estoque, 3, "PED-2")

        assert saldo(servico) == (10, 2, 8, 0)

    def test_solicitacao_id_e_obrigatoria(self, servico, sku_com_estoque) -> None:
        with pytest.raises(DadoInvalido):
            servico.reservar(sku_com_estoque, 1, "   ")

    def test_consultar_reserva_pelo_id(self, servico, sku_com_estoque) -> None:
        criada = servico.reservar(sku_com_estoque, 3, "PED-1")
        assert servico.consultar_reserva(criada.id) == criada

    def test_id_de_bloqueio_nao_e_encontrado_como_reserva(self, servico, sku_com_estoque) -> None:
        bloqueio = servico.bloquear(sku_com_estoque, 1, "INS-1")
        with pytest.raises(RetencaoNaoEncontrada):
            servico.consultar_reserva(bloqueio.id)


class TestLiberacao:
    def test_liberar_reserva_devolve_ao_disponivel(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")

        liberada = servico.liberar_reserva(reserva.id)

        assert liberada.status == StatusRetencao.LIBERADA
        assert liberada.liberada_em == INSTANTE_FIXO
        assert saldo(servico) == (10, 10, 0, 0)
        assert servico.consultar_reserva(reserva.id).status == StatusRetencao.LIBERADA

    def test_liberar_bloqueio_devolve_ao_disponivel(self, servico, sku_com_estoque) -> None:
        bloqueio = servico.bloquear(sku_com_estoque, 2, "INS-1")
        servico.liberar_bloqueio(bloqueio.id)
        assert saldo(servico) == (10, 10, 0, 0)

    def test_libera_somente_a_retencao_indicada(self, servico, sku_com_estoque) -> None:
        primeira = servico.reservar(sku_com_estoque, 2, "PED-1")
        servico.reservar(sku_com_estoque, 3, "PED-2")

        servico.liberar_reserva(primeira.id)

        assert saldo(servico) == (10, 7, 3, 0)

    def test_nao_libera_duas_vezes(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        servico.liberar_reserva(reserva.id)

        with pytest.raises(RetencaoJaLiberada):
            servico.liberar_reserva(reserva.id)

        assert saldo(servico) == (10, 10, 0, 0)  # não devolveu as 3 unidades duas vezes

    def test_liberar_id_inexistente(self, servico, sku_com_estoque) -> None:
        with pytest.raises(RetencaoNaoEncontrada):
            servico.liberar_reserva("nao-existe")

    def test_nao_libera_reserva_pela_operacao_de_bloqueio(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")

        with pytest.raises(RetencaoNaoEncontrada):
            servico.liberar_bloqueio(reserva.id)

        assert saldo(servico) == (10, 7, 3, 0)


class TestEfetivacao:
    def test_efetivar_baixa_total_e_reservado_sem_mexer_no_disponivel(
        self, servico, sku_com_estoque
    ) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        assert saldo(servico) == (10, 7, 3, 0)

        efetivada = servico.efetivar_reserva(reserva.id)

        assert efetivada.status == StatusRetencao.EFETIVADA
        assert efetivada.efetivada_em == INSTANTE_FIXO
        assert saldo(servico) == (7, 7, 0, 0)

    def test_efetiva_somente_a_reserva_indicada(self, servico, sku_com_estoque) -> None:
        primeira = servico.reservar(sku_com_estoque, 2, "PED-1")
        servico.reservar(sku_com_estoque, 3, "PED-2")

        servico.efetivar_reserva(primeira.id)

        assert saldo(servico) == (8, 5, 3, 0)

    def test_nao_efetiva_duas_vezes(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        servico.efetivar_reserva(reserva.id)

        with pytest.raises(RetencaoJaEfetivada):
            servico.efetivar_reserva(reserva.id)

        assert saldo(servico) == (7, 7, 0, 0)  # não baixou as 3 unidades duas vezes

    def test_reserva_efetivada_nao_pode_ser_liberada(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        servico.efetivar_reserva(reserva.id)

        with pytest.raises(RetencaoJaEfetivada):
            servico.liberar_reserva(reserva.id)

        assert saldo(servico) == (7, 7, 0, 0)

    def test_reserva_liberada_nao_pode_ser_efetivada(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        servico.liberar_reserva(reserva.id)

        with pytest.raises(RetencaoJaLiberada):
            servico.efetivar_reserva(reserva.id)

        assert saldo(servico) == (10, 10, 0, 0)

    def test_bloqueio_nao_pode_ser_efetivado(self, servico, sku_com_estoque) -> None:
        bloqueio = servico.bloquear(sku_com_estoque, 2, "INS-1")

        with pytest.raises(RetencaoNaoEncontrada):
            servico.efetivar_reserva(bloqueio.id)

        assert saldo(servico) == (10, 8, 0, 2)

    def test_efetivacao_entra_no_historico(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        servico.efetivar_reserva(reserva.id)

        ultima = servico.listar_movimentacoes(sku_com_estoque)[-1]

        assert (ultima.tipo, ultima.quantidade, ultima.retencao_id) == (
            TipoMovimentacao.EFETIVACAO_RESERVA,
            3,
            reserva.id,
        )


class TestMovimentacoes:
    def test_cada_operacao_gera_um_registro_no_historico(self, servico, sku_com_estoque) -> None:
        reserva = servico.reservar(sku_com_estoque, 3, "PED-1")
        bloqueio = servico.bloquear(sku_com_estoque, 2, "INS-1")
        servico.liberar_reserva(reserva.id)
        servico.liberar_bloqueio(bloqueio.id)
        servico.registrar_saida(sku_com_estoque, 4)

        historico = [
            (m.tipo, m.quantidade, m.retencao_id)
            for m in servico.listar_movimentacoes(sku_com_estoque)
        ]

        assert historico == [
            (TipoMovimentacao.ENTRADA, 10, None),
            (TipoMovimentacao.RESERVA, 3, reserva.id),
            (TipoMovimentacao.BLOQUEIO, 2, bloqueio.id),
            (TipoMovimentacao.LIBERACAO_RESERVA, 3, reserva.id),
            (TipoMovimentacao.LIBERACAO_BLOQUEIO, 2, bloqueio.id),
            (TipoMovimentacao.SAIDA, 4, None),
        ]

    def test_operacao_recusada_nao_entra_no_historico(self, servico, sku_com_estoque) -> None:
        with pytest.raises(QuantidadeIndisponivel):
            servico.registrar_saida(sku_com_estoque, 50)
        assert len(servico.listar_movimentacoes(sku_com_estoque)) == 1  # só a entrada


class TestConsistenciaEPersistencia:
    def test_falha_no_meio_da_operacao_desfaz_tudo(
        self, servico, sku_com_estoque, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simula uma falha DEPOIS de atualizar o total e ANTES de gravar o histórico.

        Sem transação, o total ficaria alterado sem registro no histórico. Com a transação,
        o rollback devolve o banco ao estado anterior.
        """

        def falhar(*args: object, **kwargs: object) -> None:
            raise RuntimeError("falha simulada (ex.: disco cheio)")

        monkeypatch.setattr(RepositorioEstoque, "registrar_movimentacao", falhar)

        with pytest.raises(RuntimeError):
            servico.registrar_entrada(sku_com_estoque, 5)

        monkeypatch.undo()
        assert saldo(servico) == (10, 10, 0, 0)
        assert len(servico.listar_movimentacoes(sku_com_estoque)) == 1

    def test_dados_sobrevivem_a_um_novo_servico_no_mesmo_arquivo(
        self, tmp_path: Path, servico: ServicoEstoque, sku_com_estoque: str
    ) -> None:
        servico.reservar(sku_com_estoque, 3, "PED-1")

        # Mesmo arquivo usado pela fixture `banco`, mas aberto do zero (como num reinício).
        novo_servico = ServicoEstoque(
            BancoDeDados(tmp_path / "estoque.db", sincronizar_disco=False)
        )

        assert saldo(novo_servico) == (10, 7, 3, 0)

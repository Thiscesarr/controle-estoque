"""Testes unitários das regras do domínio.

Não usam banco nem API: testam as regras puras, que são a parte mais crítica do sistema.
Por isso rodam em milissegundos e apontam exatamente qual regra quebrou.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from estoque.domain.errors import (
    DadoInvalido,
    QuantidadeIndisponivel,
    QuantidadeInvalida,
    RetencaoJaEfetivada,
    RetencaoJaLiberada,
)
from estoque.domain.models import (
    QUANTIDADE_MAXIMA_POR_OPERACAO,
    TAMANHO_MAXIMO_TEXTO,
    Retencao,
    SaldoEstoque,
    StatusRetencao,
    TipoRetencao,
    normalizar_sku,
    validar_quantidade,
    validar_sku,
    validar_texto,
)

INSTANTE = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def nova_reserva(**alteracoes: object) -> Retencao:
    """Cria uma reserva ativa padrão; cada teste altera só o que importa para ele."""
    dados: dict[str, object] = {
        "id": "ret-1",
        "sku": "NB-001",
        "tipo": TipoRetencao.RESERVA,
        "quantidade": 3,
        "solicitacao_id": "PED-1",
        "status": StatusRetencao.ATIVA,
        "criada_em": INSTANTE,
    }
    dados.update(alteracoes)
    return Retencao(**dados)  # type: ignore[arg-type]


class TestValidarQuantidade:
    @pytest.mark.parametrize("quantidade", [1, 10, QUANTIDADE_MAXIMA_POR_OPERACAO])
    def test_aceita_inteiros_dentro_do_intervalo(self, quantidade: int) -> None:
        assert validar_quantidade(quantidade) == quantidade

    @pytest.mark.parametrize(
        "quantidade",
        [
            0,
            -1,
            QUANTIDADE_MAXIMA_POR_OPERACAO + 1,
            2.5,  # unidades são discretas
            3.0,  # mesmo um float "redondo" não é aceito
            "3",  # texto não é convertido silenciosamente
            None,
            True,  # em Python, True é um int que vale 1
            False,
        ],
    )
    def test_rejeita_valores_invalidos(self, quantidade: object) -> None:
        with pytest.raises(QuantidadeInvalida):
            validar_quantidade(quantidade)


class TestValidarSku:
    @pytest.mark.parametrize("sku", ["NB-001", "a", "SKU_123.v2", "X" * 50])
    def test_aceita_formatos_validos_e_devolve_em_maiusculas(self, sku: str) -> None:
        assert validar_sku(sku) == sku.upper()

    def test_normalizar_sku_nao_diferencia_maiusculas(self) -> None:
        assert normalizar_sku("nb-001") == normalizar_sku("Nb-001") == "NB-001"

    def test_normalizar_sku_nao_altera_texto_com_caracteres_especiais(self) -> None:
        # "ﬀ" (ligadura) viraria "FF" com upper(); um SKU inválido não pode virar um válido.
        assert normalizar_sku("ﬀ") == "ﬀ"

    @pytest.mark.parametrize(
        "sku",
        ["", " NB-001", "NB 001", "NB/001", "../etc", "-NB", "X" * 51, "NB-001\n", None, 123],
    )
    def test_rejeita_formatos_invalidos(self, sku: object) -> None:
        with pytest.raises(DadoInvalido):
            validar_sku(sku)


class TestValidarTexto:
    def test_remove_espacos_das_pontas(self) -> None:
        assert validar_texto("  Notebook X  ", "nome") == "Notebook X"

    @pytest.mark.parametrize("valor", ["", "   ", None, 10, "x" * (TAMANHO_MAXIMO_TEXTO + 1)])
    def test_rejeita_texto_invalido(self, valor: object) -> None:
        with pytest.raises(DadoInvalido) as erro:
            validar_texto(valor, "nome")
        assert erro.value.campo == "nome"


class TestSaldoEstoque:
    def test_disponivel_e_o_total_menos_o_que_esta_retido(self) -> None:
        saldo = SaldoEstoque(quantidade_total=10, quantidade_reservada=3, quantidade_bloqueada=2)
        assert saldo.quantidade_disponivel == 5

    def test_permite_usar_exatamente_o_disponivel(self) -> None:
        saldo = SaldoEstoque(quantidade_total=10, quantidade_reservada=3, quantidade_bloqueada=2)
        saldo.garantir_disponivel(5)  # não deve lançar erro

    def test_recusa_quantidade_acima_do_disponivel_informando_os_valores(self) -> None:
        saldo = SaldoEstoque(quantidade_total=10, quantidade_reservada=3, quantidade_bloqueada=2)
        with pytest.raises(QuantidadeIndisponivel) as erro:
            saldo.garantir_disponivel(6)
        assert erro.value.solicitada == 6
        assert erro.value.disponivel == 5

    def test_e_imutavel(self) -> None:
        saldo = SaldoEstoque(quantidade_total=10, quantidade_reservada=0, quantidade_bloqueada=0)
        with pytest.raises(FrozenInstanceError):
            saldo.quantidade_total = 99  # type: ignore[misc]


class TestRetencao:
    def test_nasce_ativa(self) -> None:
        assert nova_reserva().ativa

    def test_liberar_devolve_uma_nova_retencao_liberada(self) -> None:
        original = nova_reserva()
        momento = datetime(2026, 11, 8, 15, 30, tzinfo=UTC)

        liberada = original.liberar(momento)

        assert liberada.status == StatusRetencao.LIBERADA
        assert liberada.liberada_em == momento
        assert not liberada.ativa
        assert original.ativa  # o objeto original não foi alterado

    def test_nao_pode_ser_liberada_duas_vezes(self) -> None:
        liberada = nova_reserva().liberar(INSTANTE)
        with pytest.raises(RetencaoJaLiberada):
            liberada.liberar(INSTANTE)

    def test_efetivar_devolve_uma_nova_reserva_efetivada(self) -> None:
        original = nova_reserva()

        efetivada = original.efetivar(INSTANTE)

        assert efetivada.status == StatusRetencao.EFETIVADA
        assert efetivada.efetivada_em == INSTANTE
        assert efetivada.liberada_em is None
        assert original.ativa

    def test_reserva_efetivada_nao_pode_ser_liberada_nem_efetivada_de_novo(self) -> None:
        efetivada = nova_reserva().efetivar(INSTANTE)
        with pytest.raises(RetencaoJaEfetivada):
            efetivada.liberar(INSTANTE)
        with pytest.raises(RetencaoJaEfetivada):
            efetivada.efetivar(INSTANTE)

    def test_reserva_liberada_nao_pode_ser_efetivada(self) -> None:
        liberada = nova_reserva().liberar(INSTANTE)
        with pytest.raises(RetencaoJaLiberada):
            liberada.efetivar(INSTANTE)

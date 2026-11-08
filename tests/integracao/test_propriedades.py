"""Teste baseado em propriedades (Hypothesis): sequências aleatórias de operações.

Em vez de escrevermos exemplos à mão, o Hypothesis gera centenas de sequências de
operações (entradas, saídas, reservas, bloqueios e liberações, válidas e inválidas) e,
depois de CADA passo, confere se o sistema continua correto.

"Correto" aqui é comparado com um modelo de referência propositalmente simples, em
memória (um total e um dicionário de retenções ativas). Se o sistema real e o modelo
discordarem, o teste falha e o Hypothesis mostra a MENOR sequência de passos que reproduz
o problema, o que facilita muito a investigação.
"""

import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, multiple, rule

from estoque.db import BancoDeDados
from estoque.domain.errors import QuantidadeIndisponivel, RetencaoJaLiberada
from estoque.domain.models import TipoRetencao
from estoque.service import ServicoEstoque
from tests.apoio import SKU


class MaquinaDeEstoque(RuleBasedStateMachine):
    retencoes = Bundle("retencoes")  # ids de reservas/bloqueios criados durante a sequência

    def __init__(self) -> None:
        super().__init__()
        self._pasta = Path(tempfile.mkdtemp())
        banco = BancoDeDados(self._pasta / "estoque.db", sincronizar_disco=False)
        banco.criar_schema()
        self.servico = ServicoEstoque(banco)
        self.servico.cadastrar_produto(SKU, "Produto de teste")

        # Modelo de referência: o que o sistema DEVERIA ter depois de cada passo.
        self.total = 0
        self.ativas: dict[str, tuple[TipoRetencao, int]] = {}
        self.tipo_por_id: dict[str, TipoRetencao] = {}

    def teardown(self) -> None:
        shutil.rmtree(self._pasta, ignore_errors=True)

    def _retido(self, tipo: TipoRetencao) -> int:
        return sum(qtd for t, qtd in self.ativas.values() if t == tipo)

    @property
    def disponivel_esperado(self) -> int:
        return self.total - sum(qtd for _, qtd in self.ativas.values())

    # ------------------------------------------------------------- operações

    @rule(quantidade=st.integers(min_value=1, max_value=20))
    def entrada(self, quantidade: int) -> None:
        self.servico.registrar_entrada(SKU, quantidade)
        self.total += quantidade

    @rule(quantidade=st.integers(min_value=1, max_value=25))
    def saida(self, quantidade: int) -> None:
        if quantidade <= self.disponivel_esperado:
            self.servico.registrar_saida(SKU, quantidade)
            self.total -= quantidade
        else:
            with pytest.raises(QuantidadeIndisponivel):
                self.servico.registrar_saida(SKU, quantidade)

    @rule(
        target=retencoes,
        tipo=st.sampled_from(TipoRetencao),
        quantidade=st.integers(min_value=1, max_value=15),
    )
    def reter(self, tipo: TipoRetencao, quantidade: int):
        """Devolve o id criado para o Bundle (ou nada, com `multiple()`, se foi recusado)."""
        operacao = self.servico.reservar if tipo == TipoRetencao.RESERVA else self.servico.bloquear
        if quantidade > self.disponivel_esperado:
            with pytest.raises(QuantidadeIndisponivel):
                operacao(SKU, quantidade, "SOLICITACAO")
            return multiple()  # nada novo para o Bundle

        retencao = operacao(SKU, quantidade, "SOLICITACAO")
        self.ativas[retencao.id] = (tipo, quantidade)
        self.tipo_por_id[retencao.id] = tipo
        return retencao.id

    @rule(retencao_id=retencoes)
    def liberar(self, retencao_id: str) -> None:
        tipo = self.tipo_por_id[retencao_id]
        operacao = (
            self.servico.liberar_reserva
            if tipo == TipoRetencao.RESERVA
            else self.servico.liberar_bloqueio
        )
        if retencao_id in self.ativas:
            operacao(retencao_id)
            del self.ativas[retencao_id]
        else:  # o Hypothesis também tenta liberar o que já foi liberado
            with pytest.raises(RetencaoJaLiberada):
                operacao(retencao_id)

    # ------------------------------------------------- conferido a cada passo

    @invariant()
    def estoque_confere_com_o_modelo(self) -> None:
        saldo = self.servico.consultar(SKU).saldo

        assert saldo.quantidade_disponivel >= 0  # a regra que nunca pode quebrar
        assert saldo.quantidade_total == self.total
        assert saldo.quantidade_reservada == self._retido(TipoRetencao.RESERVA)
        assert saldo.quantidade_bloqueada == self._retido(TipoRetencao.BLOQUEIO)
        assert saldo.quantidade_disponivel == self.disponivel_esperado


# deadline=None: acesso a disco varia muito entre máquinas; não queremos falhas por lentidão.
MaquinaDeEstoque.TestCase.settings = settings(
    max_examples=40, stateful_step_count=25, deadline=None
)
TestSequenciasAleatorias = MaquinaDeEstoque.TestCase

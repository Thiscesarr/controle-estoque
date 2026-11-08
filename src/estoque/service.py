"""Casos de uso do estoque.

Cada método público é uma operação completa do case (entrada, saída, reservar, bloquear,
liberar, consultar). O roteiro é sempre o mesmo:

1. validar os dados recebidos (antes de tocar no banco);
2. abrir UMA transação;
3. ler a situação atual, aplicar a regra do domínio e gravar o resultado;
4. registrar a movimentação (auditoria);
5. confirmar a transação. Se qualquer passo falhar, nada é gravado.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from estoque.db import BancoDeDados
from estoque.domain.errors import ProdutoJaCadastrado, ProdutoNaoEncontrado, RetencaoNaoEncontrada
from estoque.domain.models import (
    Movimentacao,
    Produto,
    Retencao,
    SituacaoProduto,
    StatusRetencao,
    TipoMovimentacao,
    TipoRetencao,
    validar_quantidade,
    validar_sku,
    validar_texto,
)
from estoque.repository import RepositorioEstoque

_MOVIMENTACAO_AO_RETER = {
    TipoRetencao.RESERVA: TipoMovimentacao.RESERVA,
    TipoRetencao.BLOQUEIO: TipoMovimentacao.BLOQUEIO,
}
_MOVIMENTACAO_AO_LIBERAR = {
    TipoRetencao.RESERVA: TipoMovimentacao.LIBERACAO_RESERVA,
    TipoRetencao.BLOQUEIO: TipoMovimentacao.LIBERACAO_BLOQUEIO,
}


def _agora_utc() -> datetime:
    return datetime.now(UTC)


def _novo_id() -> str:
    return str(uuid4())


class ServicoEstoque:
    """Ponto de entrada das operações de estoque, usado pela API e pelos testes.

    O relógio e o gerador de ids são recebidos no construtor (com valores padrão) para que
    os testes possam fixar datas e ids e verificar resultados exatos.
    """

    def __init__(
        self,
        banco: BancoDeDados,
        relogio: Callable[[], datetime] = _agora_utc,
        gerador_id: Callable[[], str] = _novo_id,
    ) -> None:
        self._banco = banco
        self._relogio = relogio
        self._gerador_id = gerador_id

    # ------------------------------------------------------------------ produtos

    def cadastrar_produto(self, sku: str, nome: str) -> SituacaoProduto:
        produto = Produto(sku=validar_sku(sku), nome=validar_texto(nome, "nome"))
        with self._banco.escrita() as conexao:
            repositorio = RepositorioEstoque(conexao)
            if repositorio.obter_situacao(produto.sku) is not None:
                raise ProdutoJaCadastrado(produto.sku)
            repositorio.inserir_produto(produto)
            return self._situacao(repositorio, produto.sku)

    def consultar(self, sku: str) -> SituacaoProduto:
        with self._banco.leitura() as conexao:
            return self._situacao(RepositorioEstoque(conexao), sku)

    def listar_movimentacoes(self, sku: str) -> list[Movimentacao]:
        with self._banco.leitura() as conexao:
            repositorio = RepositorioEstoque(conexao)
            self._situacao(repositorio, sku)  # produto inexistente -> ProdutoNaoEncontrado
            return repositorio.listar_movimentacoes(sku)

    # ----------------------------------------------------------- entrada e saída

    def registrar_entrada(self, sku: str, quantidade: int) -> SituacaoProduto:
        quantidade = validar_quantidade(quantidade)
        with self._banco.escrita() as conexao:
            repositorio = RepositorioEstoque(conexao)
            saldo = self._situacao(repositorio, sku).saldo
            repositorio.atualizar_total(sku, saldo.quantidade_total + quantidade)
            repositorio.registrar_movimentacao(
                sku, TipoMovimentacao.ENTRADA, quantidade, self._relogio()
            )
            return self._situacao(repositorio, sku)

    def registrar_saida(self, sku: str, quantidade: int) -> SituacaoProduto:
        quantidade = validar_quantidade(quantidade)
        with self._banco.escrita() as conexao:
            repositorio = RepositorioEstoque(conexao)
            saldo = self._situacao(repositorio, sku).saldo
            saldo.garantir_disponivel(quantidade)  # reservado e bloqueado não podem sair
            repositorio.atualizar_total(sku, saldo.quantidade_total - quantidade)
            repositorio.registrar_movimentacao(
                sku, TipoMovimentacao.SAIDA, quantidade, self._relogio()
            )
            return self._situacao(repositorio, sku)

    # ------------------------------------------------------ reservas e bloqueios

    def reservar(
        self, sku: str, quantidade: int, solicitacao_id: str, motivo: str | None = None
    ) -> Retencao:
        return self._reter(TipoRetencao.RESERVA, sku, quantidade, solicitacao_id, motivo)

    def bloquear(
        self, sku: str, quantidade: int, solicitacao_id: str, motivo: str | None = None
    ) -> Retencao:
        return self._reter(TipoRetencao.BLOQUEIO, sku, quantidade, solicitacao_id, motivo)

    def consultar_reserva(self, reserva_id: str) -> Retencao:
        return self._consultar_retencao(TipoRetencao.RESERVA, reserva_id)

    def consultar_bloqueio(self, bloqueio_id: str) -> Retencao:
        return self._consultar_retencao(TipoRetencao.BLOQUEIO, bloqueio_id)

    def liberar_reserva(self, reserva_id: str) -> Retencao:
        return self._liberar(TipoRetencao.RESERVA, reserva_id)

    def liberar_bloqueio(self, bloqueio_id: str) -> Retencao:
        return self._liberar(TipoRetencao.BLOQUEIO, bloqueio_id)

    def efetivar_reserva(self, reserva_id: str) -> Retencao:
        """Entrega do pedido: as unidades reservadas saem do estoque numa única transação.

        O total cai e o reservado cai na mesma quantidade, então o disponível não muda.
        Fazer isso em uma operação só evita a brecha de "liberar e depois dar saída", em que
        outra requisição poderia usar as unidades no intervalo entre as duas chamadas.
        """
        with self._banco.escrita() as conexao:
            repositorio = RepositorioEstoque(conexao)
            reserva = self._buscar_retencao(repositorio, TipoRetencao.RESERVA, reserva_id)

            agora = self._relogio()
            efetivada = reserva.efetivar(agora)  # já liberada/efetivada -> erro 409
            saldo = self._situacao(repositorio, efetivada.sku).saldo
            repositorio.atualizar_total(
                efetivada.sku, saldo.quantidade_total - efetivada.quantidade
            )
            repositorio.atualizar_retencao(efetivada)
            repositorio.registrar_movimentacao(
                efetivada.sku,
                TipoMovimentacao.EFETIVACAO_RESERVA,
                efetivada.quantidade,
                agora,
                efetivada.id,
            )
            return efetivada

    # ------------------------------------------------------------------- internos

    def _reter(
        self,
        tipo: TipoRetencao,
        sku: str,
        quantidade: int,
        solicitacao_id: str,
        motivo: str | None,
    ) -> Retencao:
        """Reservar e bloquear são a mesma operação; só muda o tipo da retenção."""
        quantidade = validar_quantidade(quantidade)
        solicitacao_id = validar_texto(solicitacao_id, "solicitacao_id")
        motivo = None if motivo is None else validar_texto(motivo, "motivo")

        with self._banco.escrita() as conexao:
            repositorio = RepositorioEstoque(conexao)
            saldo = self._situacao(repositorio, sku).saldo
            saldo.garantir_disponivel(quantidade)

            agora = self._relogio()
            retencao = Retencao(
                id=self._gerador_id(),
                sku=sku,
                tipo=tipo,
                quantidade=quantidade,
                solicitacao_id=solicitacao_id,
                status=StatusRetencao.ATIVA,
                criada_em=agora,
                motivo=motivo,
            )
            repositorio.inserir_retencao(retencao)
            repositorio.registrar_movimentacao(
                sku, _MOVIMENTACAO_AO_RETER[tipo], quantidade, agora, retencao.id
            )
            return retencao

    def _consultar_retencao(self, tipo: TipoRetencao, retencao_id: str) -> Retencao:
        with self._banco.leitura() as conexao:
            return self._buscar_retencao(RepositorioEstoque(conexao), tipo, retencao_id)

    def _liberar(self, tipo: TipoRetencao, retencao_id: str) -> Retencao:
        with self._banco.escrita() as conexao:
            repositorio = RepositorioEstoque(conexao)
            retencao = self._buscar_retencao(repositorio, tipo, retencao_id)

            agora = self._relogio()
            liberada = retencao.liberar(agora)  # já liberada -> RetencaoJaLiberada
            repositorio.atualizar_retencao(liberada)
            repositorio.registrar_movimentacao(
                liberada.sku,
                _MOVIMENTACAO_AO_LIBERAR[tipo],
                liberada.quantidade,
                agora,
                liberada.id,
            )
            return liberada

    @staticmethod
    def _situacao(repositorio: RepositorioEstoque, sku: str) -> SituacaoProduto:
        situacao = repositorio.obter_situacao(sku)
        if situacao is None:
            raise ProdutoNaoEncontrado(sku)
        return situacao

    @staticmethod
    def _buscar_retencao(
        repositorio: RepositorioEstoque, tipo: TipoRetencao, retencao_id: str
    ) -> Retencao:
        """Id de bloqueio usado na rota de reserva (ou vice-versa) conta como inexistente."""
        retencao = repositorio.obter_retencao(retencao_id)
        if retencao is None or retencao.tipo != tipo:
            raise RetencaoNaoEncontrada(retencao_id)
        return retencao

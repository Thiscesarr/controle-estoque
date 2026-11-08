"""Repositório: todo o SQL da aplicação fica neste arquivo.

O repositório traduz entre o mundo do banco (linhas e colunas) e o mundo do domínio
(Produto, SaldoEstoque, Retencao...). Ele não decide regras de negócio: só lê e grava.

Ele recebe uma conexão que já está dentro de uma transação (aberta pelo serviço), então
todas as leituras e escritas de uma operação fazem parte da mesma transação.

Os valores sempre vão para o SQL como parâmetros (`?`), nunca colados no texto do
comando. Isso impede SQL injection.
"""

import sqlite3
from datetime import datetime

from estoque.domain.models import (
    Movimentacao,
    Produto,
    Retencao,
    SaldoEstoque,
    SituacaoProduto,
    StatusRetencao,
    TipoMovimentacao,
    TipoRetencao,
)

# Uma única consulta traz o produto e soma as retenções ativas por tipo.
# LEFT JOIN: um produto sem nenhuma retenção também aparece (com somas zeradas).
_SQL_SITUACAO = """
SELECT p.sku,
       p.nome,
       p.quantidade_total,
       COALESCE(SUM(CASE WHEN r.tipo = 'RESERVA' THEN r.quantidade END), 0)  AS reservada,
       COALESCE(SUM(CASE WHEN r.tipo = 'BLOQUEIO' THEN r.quantidade END), 0) AS bloqueada
  FROM produtos AS p
  LEFT JOIN retencoes AS r
         ON r.sku = p.sku
        AND r.status = 'ATIVA'
 WHERE p.sku = ?
 GROUP BY p.sku
"""


class RepositorioEstoque:
    def __init__(self, conexao: sqlite3.Connection) -> None:
        self._conexao = conexao

    # ----------------------------------------------------------------- produtos

    def inserir_produto(self, produto: Produto) -> None:
        self._conexao.execute(
            "INSERT INTO produtos (sku, nome, quantidade_total) VALUES (?, ?, 0)",
            (produto.sku, produto.nome),
        )

    def obter_situacao(self, sku: str) -> SituacaoProduto | None:
        """Produto + saldo lidos juntos. Devolve None se o produto não existir."""
        linha = self._conexao.execute(_SQL_SITUACAO, (sku,)).fetchone()
        if linha is None:
            return None
        return SituacaoProduto(
            produto=Produto(sku=linha["sku"], nome=linha["nome"]),
            saldo=SaldoEstoque(
                quantidade_total=linha["quantidade_total"],
                quantidade_reservada=linha["reservada"],
                quantidade_bloqueada=linha["bloqueada"],
            ),
        )

    def atualizar_total(self, sku: str, quantidade_total: int) -> None:
        self._conexao.execute(
            "UPDATE produtos SET quantidade_total = ? WHERE sku = ?",
            (quantidade_total, sku),
        )

    # ---------------------------------------------------------------- retenções

    def inserir_retencao(self, retencao: Retencao) -> None:
        self._conexao.execute(
            """
            INSERT INTO retencoes (id, sku, tipo, quantidade, solicitacao_id, motivo,
                                   status, criada_em, liberada_em, efetivada_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                retencao.id,
                retencao.sku,
                retencao.tipo.value,
                retencao.quantidade,
                retencao.solicitacao_id,
                retencao.motivo,
                retencao.status.value,
                _texto_de_data(retencao.criada_em),
                _texto_de_data(retencao.liberada_em),
                _texto_de_data(retencao.efetivada_em),
            ),
        )

    def obter_retencao(self, retencao_id: str) -> Retencao | None:
        linha = self._conexao.execute(
            "SELECT * FROM retencoes WHERE id = ?", (retencao_id,)
        ).fetchone()
        return None if linha is None else _retencao_de_linha(linha)

    def listar_retencoes(
        self,
        sku: str,
        tipo: TipoRetencao | None = None,
        status: StatusRetencao | None = None,
        solicitacao_id: str | None = None,
    ) -> list[Retencao]:
        """Retenções do produto, das mais antigas para as mais recentes, com filtros opcionais.

        Só nomes de coluna fixos entram no texto do SQL; os valores vão como parâmetros `?`.
        """
        condicoes, parametros = ["sku = ?"], [sku]
        filtros = {"tipo": tipo, "status": status, "solicitacao_id": solicitacao_id}
        for coluna, valor in filtros.items():
            if valor is not None:
                condicoes.append(f"{coluna} = ?")
                parametros.append(str(valor))
        linhas = self._conexao.execute(
            f"SELECT * FROM retencoes WHERE {' AND '.join(condicoes)} ORDER BY criada_em, id",
            parametros,
        ).fetchall()
        return [_retencao_de_linha(linha) for linha in linhas]

    def atualizar_retencao(self, retencao: Retencao) -> None:
        """Grava a mudança de estado de uma retenção (liberação ou efetivação)."""
        self._conexao.execute(
            "UPDATE retencoes SET status = ?, liberada_em = ?, efetivada_em = ? WHERE id = ?",
            (
                retencao.status.value,
                _texto_de_data(retencao.liberada_em),
                _texto_de_data(retencao.efetivada_em),
                retencao.id,
            ),
        )

    # ------------------------------------------------------------ movimentações

    def registrar_movimentacao(
        self,
        sku: str,
        tipo: TipoMovimentacao,
        quantidade: int,
        ocorrida_em: datetime,
        retencao_id: str | None = None,
    ) -> None:
        self._conexao.execute(
            """
            INSERT INTO movimentacoes (sku, tipo, quantidade, retencao_id, ocorrida_em)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sku, tipo.value, quantidade, retencao_id, _texto_de_data(ocorrida_em)),
        )

    def listar_movimentacoes(self, sku: str) -> list[Movimentacao]:
        """Histórico do produto, do mais antigo para o mais recente."""
        linhas = self._conexao.execute(
            "SELECT * FROM movimentacoes WHERE sku = ? ORDER BY id", (sku,)
        ).fetchall()
        return [
            Movimentacao(
                id=linha["id"],
                sku=linha["sku"],
                tipo=TipoMovimentacao(linha["tipo"]),
                quantidade=linha["quantidade"],
                ocorrida_em=_data_de_texto(linha["ocorrida_em"]),
                retencao_id=linha["retencao_id"],
            )
            for linha in linhas
        ]


def _retencao_de_linha(linha: sqlite3.Row) -> Retencao:
    return Retencao(
        id=linha["id"],
        sku=linha["sku"],
        tipo=TipoRetencao(linha["tipo"]),
        quantidade=linha["quantidade"],
        solicitacao_id=linha["solicitacao_id"],
        status=StatusRetencao(linha["status"]),
        criada_em=_data_de_texto(linha["criada_em"]),
        motivo=linha["motivo"],
        liberada_em=_data_opcional_de_texto(linha["liberada_em"]),
        efetivada_em=_data_opcional_de_texto(linha["efetivada_em"]),
    )


# Datas são gravadas como texto ISO 8601 com fuso (ex.: 2026-11-08T12:00:00+00:00).
# É legível, ordenável e não depende dos conversores automáticos do sqlite3, que estão
# obsoletos desde o Python 3.12.


def _texto_de_data(data: datetime | None) -> str | None:
    return None if data is None else data.isoformat()


def _data_de_texto(texto: str) -> datetime:
    return datetime.fromisoformat(texto)


def _data_opcional_de_texto(texto: str | None) -> datetime | None:
    return None if texto is None else _data_de_texto(texto)

"""Acesso ao banco SQLite: criação das tabelas, conexões e transações.

Regra de uso: toda operação do serviço abre UMA transação aqui e faz tudo dentro dela.
Se qualquer passo falhar, a transação inteira é desfeita (rollback) e o estoque continua
exatamente como estava antes.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

TEMPO_ESPERA_LOCK_SEGUNDOS = 5.0
"""Quanto tempo uma operação espera o lock de escrita antes de desistir com erro."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    sku              TEXT PRIMARY KEY,
    nome             TEXT NOT NULL,
    quantidade_total INTEGER NOT NULL DEFAULT 0 CHECK (quantidade_total >= 0)
);

CREATE TABLE IF NOT EXISTS retencoes (
    id             TEXT PRIMARY KEY,
    sku            TEXT NOT NULL REFERENCES produtos (sku),
    tipo           TEXT NOT NULL CHECK (tipo IN ('RESERVA', 'BLOQUEIO')),
    quantidade     INTEGER NOT NULL CHECK (quantidade > 0),
    solicitacao_id TEXT NOT NULL,
    motivo         TEXT,
    status         TEXT NOT NULL CHECK (status IN ('ATIVA', 'LIBERADA')),
    criada_em      TEXT NOT NULL,
    liberada_em    TEXT
);

-- Acelera a soma das retenções ativas de um produto, feita em toda operação.
CREATE INDEX IF NOT EXISTS idx_retencoes_sku_status ON retencoes (sku, status);

CREATE TABLE IF NOT EXISTS movimentacoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sku         TEXT NOT NULL REFERENCES produtos (sku),
    tipo        TEXT NOT NULL,
    quantidade  INTEGER NOT NULL CHECK (quantidade > 0),
    retencao_id TEXT REFERENCES retencoes (id),
    ocorrida_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_movimentacoes_sku ON movimentacoes (sku, id);
"""


class BancoDeDados:
    """Sabe onde está o arquivo do banco e entrega conexões já dentro de uma transação."""

    def __init__(self, caminho: str | Path) -> None:
        self._caminho = str(caminho)

    def criar_schema(self) -> None:
        """Cria as tabelas, se ainda não existirem. Pode ser chamado várias vezes."""
        with self._conexao() as conexao:
            # WAL: leitores não bloqueiam o escritor (e vice-versa). Fica gravado no arquivo.
            conexao.execute("PRAGMA journal_mode = WAL")
            conexao.executescript(SCHEMA)

    @contextmanager
    def escrita(self) -> Iterator[sqlite3.Connection]:
        """Transação de escrita: pega o lock de escrita ANTES de ler qualquer coisa.

        `BEGIN IMMEDIATE` faz as operações de escrita acontecerem uma de cada vez. Assim, duas
        reservas simultâneas nunca leem o mesmo saldo: a segunda espera a primeira terminar
        e já enxerga o disponível atualizado.
        """
        with self._transacao("BEGIN IMMEDIATE") as conexao:
            yield conexao

    @contextmanager
    def leitura(self) -> Iterator[sqlite3.Connection]:
        """Transação de leitura: todas as consultas enxergam o mesmo retrato do banco."""
        with self._transacao("BEGIN") as conexao:
            yield conexao

    @contextmanager
    def _transacao(self, comando_inicio: str) -> Iterator[sqlite3.Connection]:
        with self._conexao() as conexao:
            conexao.execute(comando_inicio)
            try:
                yield conexao
            except BaseException:
                # Qualquer erro (de negócio ou inesperado) desfaz tudo o que foi feito.
                conexao.execute("ROLLBACK")
                raise
            else:
                conexao.execute("COMMIT")

    @contextmanager
    def _conexao(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: o módulo sqlite3 não abre transações sozinho; quem controla
        # BEGIN/COMMIT/ROLLBACK é este arquivo, de forma explícita.
        conexao = sqlite3.connect(
            self._caminho, timeout=TEMPO_ESPERA_LOCK_SEGUNDOS, isolation_level=None
        )
        try:
            conexao.row_factory = sqlite3.Row  # permite ler colunas pelo nome: linha["sku"]
            conexao.execute("PRAGMA foreign_keys = ON")  # o SQLite vem com isso desligado
            yield conexao
        finally:
            # Atenção: usar `with sqlite3.connect(...)` NÃO fecha a conexão, só faz commit.
            conexao.close()

"""Fixtures compartilhadas pelos testes.

Uma fixture é uma "preparação" que o pytest entrega pronta para o teste que a pedir pelo
nome do parâmetro. Cada teste recebe um banco NOVO em uma pasta temporária (`tmp_path`),
então os testes não interferem uns nos outros e não sujam o projeto.
"""

from collections.abc import Iterator
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from estoque.api.app import criar_app
from estoque.db import BancoDeDados
from estoque.service import ServicoEstoque
from tests.apoio import INSTANTE_FIXO, SKU


@pytest.fixture
def banco(tmp_path: Path) -> BancoDeDados:
    banco = BancoDeDados(tmp_path / "estoque.db", sincronizar_disco=False)
    banco.criar_schema()
    return banco


@pytest.fixture
def servico(banco: BancoDeDados) -> ServicoEstoque:
    """Serviço com relógio fixo e ids previsíveis (ret-1, ret-2...)."""
    sequencia = count(1)
    return ServicoEstoque(
        banco,
        relogio=lambda: INSTANTE_FIXO,
        gerador_id=lambda: f"ret-{next(sequencia)}",
    )


@pytest.fixture
def sku_com_estoque(servico: ServicoEstoque) -> str:
    """Produto já cadastrado com 10 unidades em estoque (cenário mais comum)."""
    servico.cadastrar_produto(SKU, "Notebook X")
    servico.registrar_entrada(SKU, 10)
    return SKU


@pytest.fixture
def cliente(tmp_path: Path) -> Iterator[TestClient]:
    """Cliente HTTP de teste: chama a API sem subir um servidor de verdade.

    O `with` dispara o ciclo de vida da aplicação (criação do banco na inicialização).
    """
    app = criar_app(tmp_path / "api.db", sincronizar_disco=False)
    with TestClient(app) as cliente:
        yield cliente

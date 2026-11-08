"""Montagem da aplicação FastAPI: configuração, criação do serviço e tradução de erros.

`criar_app` é uma "fábrica": cada chamada monta uma aplicação nova. Os testes usam isso
para ter uma aplicação com um banco temporário próprio.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from estoque.api.routes import router
from estoque.db import BancoDeDados
from estoque.domain.errors import (
    DadoInvalido,
    ErroDominio,
    ProdutoJaCadastrado,
    ProdutoNaoEncontrado,
    QuantidadeIndisponivel,
    QuantidadeInvalida,
    RetencaoJaLiberada,
    RetencaoNaoEncontrada,
)
from estoque.service import ServicoEstoque

CAMINHO_BANCO_PADRAO = "estoque.db"

# Único lugar que sabe qual erro de negócio vira qual status HTTP.
STATUS_HTTP_POR_ERRO: dict[type[ErroDominio], int] = {
    DadoInvalido: 422,  # dado fora do formato
    QuantidadeInvalida: 422,
    ProdutoNaoEncontrado: 404,  # recurso não existe
    RetencaoNaoEncontrada: 404,
    ProdutoJaCadastrado: 409,  # conflito com o estado atual
    QuantidadeIndisponivel: 409,
    RetencaoJaLiberada: 409,
}

DESCRICAO = """
Controle de estoque com **entrada**, **saída**, **reservas** e **bloqueios**.

Regra principal: `disponível = total - reservado - bloqueado`, e nenhuma operação pode
usar mais do que o disponível. Erros seguem sempre o formato `{"codigo", "mensagem"}`.
"""


def criar_app(caminho_banco: str | Path | None = None, sincronizar_disco: bool = True) -> FastAPI:
    """Cria a aplicação. Sem caminho informado, usa a variável ESTOQUE_DB ou `estoque.db`."""
    caminho = caminho_banco or os.environ.get("ESTOQUE_DB", CAMINHO_BANCO_PADRAO)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        # Executa uma vez, quando o servidor sobe: prepara o banco e o serviço.
        banco = BancoDeDados(caminho, sincronizar_disco=sincronizar_disco)
        banco.criar_schema()
        app.state.servico = ServicoEstoque(banco)
        yield

    app = FastAPI(
        title="Controle de Estoque",
        version="0.1.0",
        description=DESCRICAO,
        lifespan=ciclo_de_vida,
    )
    app.include_router(router)
    app.add_exception_handler(ErroDominio, _tratar_erro_de_dominio)
    app.add_exception_handler(RequestValidationError, _tratar_requisicao_invalida)
    return app


async def _tratar_erro_de_dominio(request: Request, erro: ErroDominio) -> JSONResponse:
    status_http = STATUS_HTTP_POR_ERRO.get(type(erro), 400)
    return JSONResponse(
        status_code=status_http,
        content={"codigo": erro.codigo, "mensagem": erro.mensagem},
    )


async def _tratar_requisicao_invalida(
    request: Request, erro: RequestValidationError
) -> JSONResponse:
    """Erros de validação do Pydantic no mesmo formato dos erros de negócio."""
    return JSONResponse(
        status_code=422,
        content={
            "codigo": "REQUISICAO_INVALIDA",
            "mensagem": "Os dados enviados não seguem o contrato da API",
            "detalhes": jsonable_encoder(erro.errors()),
        },
    )

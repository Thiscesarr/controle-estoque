"""Montagem da aplicação FastAPI: configuração, criação do serviço e tradução de erros.

`criar_app` é uma "fábrica": cada chamada monta uma aplicação nova. Os testes usam isso
para ter uma aplicação com um banco temporário próprio.
"""

import logging
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from estoque.api.routes import router
from estoque.db import BancoDeDados
from estoque.domain.errors import (
    DadoInvalido,
    ErroDominio,
    ProdutoJaCadastrado,
    ProdutoNaoEncontrado,
    QuantidadeIndisponivel,
    QuantidadeInvalida,
    RetencaoJaEfetivada,
    RetencaoJaLiberada,
    RetencaoNaoEncontrada,
)
from estoque.service import ServicoEstoque

CAMINHO_BANCO_PADRAO = "estoque.db"
SEGUNDOS_PARA_NOVA_TENTATIVA = "1"

logger = logging.getLogger("estoque")

# Único lugar que sabe qual erro de negócio vira qual status HTTP.
STATUS_HTTP_POR_ERRO: dict[type[ErroDominio], int] = {
    DadoInvalido: 422,  # dado fora do formato
    QuantidadeInvalida: 422,
    ProdutoNaoEncontrado: 404,  # recurso não existe
    RetencaoNaoEncontrada: 404,
    ProdutoJaCadastrado: 409,  # conflito com o estado atual
    QuantidadeIndisponivel: 409,
    RetencaoJaLiberada: 409,
    RetencaoJaEfetivada: 409,
}

# Erros de roteamento (gerados pelo próprio framework), no mesmo formato dos de negócio.
ERROS_HTTP: dict[int, tuple[str, str]] = {
    404: ("ROTA_NAO_ENCONTRADA", "Rota não encontrada"),
    405: ("METODO_NAO_PERMITIDO", "Método HTTP não permitido para esta rota"),
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
    app.add_exception_handler(StarletteHTTPException, _tratar_erro_http)
    app.add_exception_handler(sqlite3.OperationalError, _tratar_erro_de_banco)
    app.add_exception_handler(Exception, _tratar_erro_inesperado)
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


async def _tratar_erro_http(request: Request, erro: StarletteHTTPException) -> JSONResponse:
    """Rota inexistente (404), método errado (405) etc. também saem como {codigo, mensagem}.

    Os cabeçalhos do erro são mantidos: no 405, o `Allow` informa os métodos aceitos.
    """
    codigo, mensagem = ERROS_HTTP.get(erro.status_code, ("ERRO_HTTP", str(erro.detail)))
    return JSONResponse(
        status_code=erro.status_code,
        content={"codigo": codigo, "mensagem": mensagem},
        headers=erro.headers,
    )


async def _tratar_erro_de_banco(request: Request, erro: sqlite3.OperationalError) -> JSONResponse:
    """Banco ocupado além do tempo de espera vira 503: a operação não foi aplicada e pode
    ser repetida. Qualquer outro erro operacional do banco é tratado como erro interno."""
    if "locked" not in str(erro).lower():
        return await _tratar_erro_inesperado(request, erro)
    return JSONResponse(
        status_code=503,
        content={
            "codigo": "BANCO_OCUPADO",
            "mensagem": "Banco ocupado por outras operações; tente novamente",
        },
        headers={"Retry-After": SEGUNDOS_PARA_NOVA_TENTATIVA},
    )


async def _tratar_erro_inesperado(request: Request, erro: Exception) -> JSONResponse:
    """Erro não previsto: registra o detalhe no log e responde no contrato da API, sem expor
    a mensagem interna ao cliente."""
    logger.error("Erro inesperado em %s %s", request.method, request.url.path, exc_info=erro)
    return JSONResponse(
        status_code=500,
        content={"codigo": "ERRO_INTERNO", "mensagem": "Erro interno inesperado"},
    )

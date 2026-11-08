"""Rotas HTTP: cada função recebe a requisição, chama o serviço e devolve a resposta.

As rotas são finas de propósito: não têm regra de negócio. Elas só traduzem HTTP para
chamadas do serviço e o resultado de volta para JSON. Os erros de negócio são convertidos
em status HTTP em um único lugar (app.py).

As funções são `def` (e não `async def`) porque o acesso ao SQLite é bloqueante. O FastAPI
executa rotas `def` em um pool de threads, sem travar o servidor para as outras requisições.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status as http_status

from estoque.api.schemas import (
    ErroResposta,
    MovimentacaoResposta,
    Movimento,
    ProdutoCriar,
    RetencaoCriar,
    RetencaoResposta,
    SituacaoResposta,
)
from estoque.domain.models import StatusRetencao, TipoRetencao
from estoque.service import ServicoEstoque


def obter_servico(request: Request) -> ServicoEstoque:
    """Injeção de dependência: entrega às rotas o serviço criado na inicialização da app."""
    return request.app.state.servico


Servico = Annotated[ServicoEstoque, Depends(obter_servico)]

router = APIRouter()


def _erros(*codigos: int) -> dict[int | str, dict[str, Any]]:
    """Documenta no Swagger os status de erro possíveis de cada rota."""
    descricoes = {
        404: "Produto, reserva ou bloqueio não encontrado",
        409: "Conflito com o estado atual do estoque",
        422: "Dados inválidos",
    }
    return {c: {"model": ErroResposta, "description": descricoes[c]} for c in codigos}


# ------------------------------------------------------------------- produtos


@router.post(
    "/produtos",
    status_code=http_status.HTTP_201_CREATED,
    tags=["Produtos"],
    summary="Cadastrar produto",
    responses=_erros(409, 422),
)
def cadastrar_produto(dados: ProdutoCriar, servico: Servico) -> SituacaoResposta:
    return SituacaoResposta.de_dominio(servico.cadastrar_produto(dados.sku, dados.nome))


@router.get(
    "/produtos/{sku}",
    tags=["Produtos"],
    summary="Consultar a situação do estoque de um produto",
    responses=_erros(404),
)
def consultar_produto(sku: str, servico: Servico) -> SituacaoResposta:
    return SituacaoResposta.de_dominio(servico.consultar(sku))


@router.post(
    "/produtos/{sku}/entradas",
    tags=["Produtos"],
    summary="Registrar entrada de unidades",
    responses=_erros(404, 422),
)
def registrar_entrada(sku: str, dados: Movimento, servico: Servico) -> SituacaoResposta:
    return SituacaoResposta.de_dominio(servico.registrar_entrada(sku, dados.quantidade))


@router.post(
    "/produtos/{sku}/saidas",
    tags=["Produtos"],
    summary="Registrar saída de unidades (somente do disponível)",
    responses=_erros(404, 409, 422),
)
def registrar_saida(sku: str, dados: Movimento, servico: Servico) -> SituacaoResposta:
    return SituacaoResposta.de_dominio(servico.registrar_saida(sku, dados.quantidade))


@router.get(
    "/produtos/{sku}/movimentacoes",
    tags=["Produtos"],
    summary="Histórico de movimentações do produto (auditoria)",
    responses=_erros(404),
)
def listar_movimentacoes(sku: str, servico: Servico) -> list[MovimentacaoResposta]:
    return [MovimentacaoResposta.de_dominio(m) for m in servico.listar_movimentacoes(sku)]


@router.get(
    "/produtos/{sku}/retencoes",
    tags=["Produtos"],
    summary="Listar reservas e bloqueios do produto (filtros opcionais)",
    responses=_erros(404, 422),
)
def listar_retencoes(
    sku: str,
    servico: Servico,
    tipo: TipoRetencao | None = None,
    status: StatusRetencao | None = None,
    solicitacao_id: str | None = None,
) -> list[RetencaoResposta]:
    retencoes = servico.listar_retencoes(sku, tipo, status, solicitacao_id)
    return [RetencaoResposta.de_dominio(r) for r in retencoes]


# ------------------------------------------------------------------- reservas


@router.post(
    "/produtos/{sku}/reservas",
    status_code=http_status.HTTP_201_CREATED,
    tags=["Reservas"],
    summary="Reservar unidades disponíveis para uma solicitação",
    responses=_erros(404, 409, 422),
)
def reservar(
    sku: str, dados: RetencaoCriar, servico: Servico, response: Response
) -> RetencaoResposta:
    reserva = servico.reservar(sku, dados.quantidade, dados.solicitacao_id, dados.motivo)
    response.headers["Location"] = f"/reservas/{reserva.id}"
    return RetencaoResposta.de_dominio(reserva)


@router.get(
    "/reservas/{reserva_id}",
    tags=["Reservas"],
    summary="Consultar uma reserva",
    responses=_erros(404),
)
def consultar_reserva(reserva_id: str, servico: Servico) -> RetencaoResposta:
    return RetencaoResposta.de_dominio(servico.consultar_reserva(reserva_id))


@router.post(
    "/reservas/{reserva_id}/liberar",
    tags=["Reservas"],
    summary="Liberar uma reserva (as unidades voltam ao disponível)",
    responses=_erros(404, 409),
)
def liberar_reserva(reserva_id: str, servico: Servico) -> RetencaoResposta:
    return RetencaoResposta.de_dominio(servico.liberar_reserva(reserva_id))


@router.post(
    "/reservas/{reserva_id}/efetivar",
    tags=["Reservas"],
    summary="Efetivar uma reserva (pedido entregue: as unidades saem do estoque)",
    responses=_erros(404, 409),
)
def efetivar_reserva(reserva_id: str, servico: Servico) -> RetencaoResposta:
    return RetencaoResposta.de_dominio(servico.efetivar_reserva(reserva_id))


# ------------------------------------------------------------------ bloqueios


@router.post(
    "/produtos/{sku}/bloqueios",
    status_code=http_status.HTTP_201_CREATED,
    tags=["Bloqueios"],
    summary="Bloquear unidades disponíveis",
    responses=_erros(404, 409, 422),
)
def bloquear(
    sku: str, dados: RetencaoCriar, servico: Servico, response: Response
) -> RetencaoResposta:
    bloqueio = servico.bloquear(sku, dados.quantidade, dados.solicitacao_id, dados.motivo)
    response.headers["Location"] = f"/bloqueios/{bloqueio.id}"
    return RetencaoResposta.de_dominio(bloqueio)


@router.get(
    "/bloqueios/{bloqueio_id}",
    tags=["Bloqueios"],
    summary="Consultar um bloqueio",
    responses=_erros(404),
)
def consultar_bloqueio(bloqueio_id: str, servico: Servico) -> RetencaoResposta:
    return RetencaoResposta.de_dominio(servico.consultar_bloqueio(bloqueio_id))


@router.post(
    "/bloqueios/{bloqueio_id}/liberar",
    tags=["Bloqueios"],
    summary="Liberar um bloqueio (as unidades voltam ao disponível)",
    responses=_erros(404, 409),
)
def liberar_bloqueio(bloqueio_id: str, servico: Servico) -> RetencaoResposta:
    return RetencaoResposta.de_dominio(servico.liberar_bloqueio(bloqueio_id))


@router.post(
    "/bloqueios/{bloqueio_id}/baixar",
    tags=["Bloqueios"],
    summary="Baixar um bloqueio (perda definitiva: as unidades saem do estoque)",
    responses=_erros(404, 409),
)
def baixar_bloqueio(bloqueio_id: str, servico: Servico) -> RetencaoResposta:
    return RetencaoResposta.de_dominio(servico.baixar_bloqueio(bloqueio_id))


# --------------------------------------------------------------------- saúde


@router.get("/saude", tags=["Operação"], summary="Verificar se a API está no ar")
def saude() -> dict[str, str]:
    return {"status": "ok"}

"""Erros do domínio de estoque.

Cada situação de negócio que impede uma operação tem um erro com nome próprio. Assim,
quem chama (o serviço, a API, os testes) sabe exatamente o que aconteceu sem precisar
interpretar textos de mensagem.

Os erros não sabem nada de HTTP: a tradução para status 404/409/422 fica na camada de API.
"""


class ErroDominio(Exception):
    """Base de todos os erros de negócio.

    `codigo` é um identificador estável, pensado para máquinas (ex.: o front-end decidir o
    que mostrar). `mensagem` é o texto legível para pessoas.
    """

    codigo = "ERRO_DOMINIO"

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


class DadoInvalido(ErroDominio):
    """Um campo informado não respeita o formato esperado (ex.: SKU com espaço)."""

    codigo = "DADO_INVALIDO"

    def __init__(self, campo: str, detalhe: str) -> None:
        super().__init__(f"Campo '{campo}' inválido: {detalhe}")
        self.campo = campo


class QuantidadeInvalida(ErroDominio):
    """A quantidade informada não é um número inteiro dentro do intervalo aceito."""

    codigo = "QUANTIDADE_INVALIDA"

    def __init__(self, quantidade: object, maximo: int) -> None:
        super().__init__(
            f"A quantidade deve ser um número inteiro entre 1 e {maximo} (recebido: {quantidade!r})"
        )
        self.quantidade = quantidade


class QuantidadeIndisponivel(ErroDominio):
    """A operação pede mais unidades do que o produto tem disponíveis."""

    codigo = "QUANTIDADE_INDISPONIVEL"

    def __init__(self, solicitada: int, disponivel: int) -> None:
        super().__init__(
            f"Quantidade solicitada ({solicitada}) maior que a disponível ({disponivel})"
        )
        self.solicitada = solicitada
        self.disponivel = disponivel


class ProdutoNaoEncontrado(ErroDominio):
    codigo = "PRODUTO_NAO_ENCONTRADO"

    def __init__(self, sku: str) -> None:
        super().__init__(f"Produto '{sku}' não encontrado")
        self.sku = sku


class ProdutoJaCadastrado(ErroDominio):
    codigo = "PRODUTO_JA_CADASTRADO"

    def __init__(self, sku: str) -> None:
        super().__init__(f"Produto '{sku}' já está cadastrado")
        self.sku = sku


class RetencaoNaoEncontrada(ErroDominio):
    codigo = "RETENCAO_NAO_ENCONTRADA"

    def __init__(self, retencao_id: str) -> None:
        super().__init__(f"Reserva/bloqueio '{retencao_id}' não encontrado")
        self.retencao_id = retencao_id


class RetencaoJaLiberada(ErroDominio):
    """Uma retenção só pode ser liberada uma vez."""

    codigo = "RETENCAO_JA_LIBERADA"

    def __init__(self, retencao_id: str) -> None:
        super().__init__(f"Reserva/bloqueio '{retencao_id}' já foi liberado")
        self.retencao_id = retencao_id

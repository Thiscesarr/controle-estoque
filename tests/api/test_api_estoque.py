"""Testes da API: o contrato HTTP (rotas, status, formato do JSON e dos erros).

As regras de negócio já são cobertas nos testes de integração do serviço. Aqui o foco é
a tradução para HTTP: o status certo para cada situação e o formato de resposta prometido.
"""

import pytest
from fastapi.testclient import TestClient


def cadastrar(cliente: TestClient, sku: str = "NB-001", estoque: int = 10) -> str:
    assert cliente.post("/produtos", json={"sku": sku, "nome": "Notebook X"}).status_code == 201
    if estoque:
        cliente.post(f"/produtos/{sku}/entradas", json={"quantidade": estoque})
    return sku


def quantidades(cliente: TestClient, sku: str = "NB-001") -> dict[str, int]:
    corpo = cliente.get(f"/produtos/{sku}").json()
    return {chave: valor for chave, valor in corpo.items() if chave.startswith("quantidade_")}


def test_fluxo_completo_do_case(cliente: TestClient) -> None:
    """A história do centro de distribuição, passo a passo, pela API."""
    resposta = cliente.post("/produtos", json={"sku": "NB-001", "nome": "Notebook X"})
    assert resposta.status_code == 201
    assert resposta.json()["quantidade_total"] == 0

    assert cliente.post("/produtos/NB-001/entradas", json={"quantidade": 10}).status_code == 200

    reserva = cliente.post(
        "/produtos/NB-001/reservas", json={"quantidade": 3, "solicitacao_id": "PED-1"}
    )
    assert reserva.status_code == 201
    reserva_id = reserva.json()["id"]
    assert reserva.headers["Location"] == f"/reservas/{reserva_id}"

    bloqueio = cliente.post(
        "/produtos/NB-001/bloqueios",
        json={"quantidade": 2, "solicitacao_id": "INS-1", "motivo": "caixa amassada"},
    )
    assert bloqueio.status_code == 201
    bloqueio_id = bloqueio.json()["id"]

    assert quantidades(cliente) == {
        "quantidade_total": 10,
        "quantidade_disponivel": 5,
        "quantidade_reservada": 3,
        "quantidade_bloqueada": 2,
    }

    saida_excessiva = cliente.post("/produtos/NB-001/saidas", json={"quantidade": 6})
    assert saida_excessiva.status_code == 409

    assert cliente.post(f"/reservas/{reserva_id}/liberar").status_code == 200
    assert cliente.post(f"/bloqueios/{bloqueio_id}/liberar").status_code == 200

    saida = cliente.post("/produtos/NB-001/saidas", json={"quantidade": 4})
    assert saida.status_code == 200
    assert saida.json() == {
        "sku": "NB-001",
        "nome": "Notebook X",
        "quantidade_total": 6,
        "quantidade_disponivel": 6,
        "quantidade_reservada": 0,
        "quantidade_bloqueada": 0,
    }

    tipos = [m["tipo"] for m in cliente.get("/produtos/NB-001/movimentacoes").json()]
    assert tipos == [
        "ENTRADA",
        "RESERVA",
        "BLOQUEIO",
        "LIBERACAO_RESERVA",
        "LIBERACAO_BLOQUEIO",
        "SAIDA",
    ]


class TestFormatoDosErros:
    def test_erro_de_negocio_tem_codigo_e_mensagem(self, cliente: TestClient) -> None:
        cadastrar(cliente)
        resposta = cliente.post("/produtos/NB-001/saidas", json={"quantidade": 11})

        assert resposta.status_code == 409
        assert resposta.json() == {
            "codigo": "QUANTIDADE_INDISPONIVEL",
            "mensagem": "Quantidade solicitada (11) maior que a disponível (10)",
        }

    def test_erro_de_validacao_usa_o_mesmo_formato(self, cliente: TestClient) -> None:
        cadastrar(cliente)
        resposta = cliente.post("/produtos/NB-001/entradas", json={"quantidade": 0})

        assert resposta.status_code == 422
        corpo = resposta.json()
        assert corpo["codigo"] == "REQUISICAO_INVALIDA"
        assert corpo["detalhes"][0]["loc"] == ["body", "quantidade"]


class TestStatusHttp:
    def test_produto_inexistente_responde_404(self, cliente: TestClient) -> None:
        for rota in ("/produtos/NAO-EXISTE", "/produtos/NAO-EXISTE/movimentacoes"):
            resposta = cliente.get(rota)
            assert resposta.status_code == 404
            assert resposta.json()["codigo"] == "PRODUTO_NAO_ENCONTRADO"

    def test_operacao_em_produto_inexistente_responde_404(self, cliente: TestClient) -> None:
        resposta = cliente.post("/produtos/NAO-EXISTE/entradas", json={"quantidade": 1})
        assert resposta.status_code == 404

    def test_sku_duplicado_responde_409(self, cliente: TestClient) -> None:
        cadastrar(cliente, estoque=0)
        resposta = cliente.post("/produtos", json={"sku": "NB-001", "nome": "Outro"})
        assert resposta.status_code == 409
        assert resposta.json()["codigo"] == "PRODUTO_JA_CADASTRADO"

    @pytest.mark.parametrize("operacao", ["reservas", "bloqueios"])
    def test_retencao_acima_do_disponivel_responde_409(
        self, cliente: TestClient, operacao: str
    ) -> None:
        cadastrar(cliente)
        resposta = cliente.post(
            f"/produtos/NB-001/{operacao}", json={"quantidade": 11, "solicitacao_id": "S-1"}
        )
        assert resposta.status_code == 409
        assert quantidades(cliente)["quantidade_disponivel"] == 10

    def test_liberar_duas_vezes_responde_409(self, cliente: TestClient) -> None:
        cadastrar(cliente)
        reserva_id = cliente.post(
            "/produtos/NB-001/reservas", json={"quantidade": 3, "solicitacao_id": "PED-1"}
        ).json()["id"]

        assert cliente.post(f"/reservas/{reserva_id}/liberar").status_code == 200
        segunda = cliente.post(f"/reservas/{reserva_id}/liberar")

        assert segunda.status_code == 409
        assert segunda.json()["codigo"] == "RETENCAO_JA_LIBERADA"

    def test_reserva_inexistente_responde_404(self, cliente: TestClient) -> None:
        assert cliente.get("/reservas/nao-existe").status_code == 404
        assert cliente.post("/reservas/nao-existe/liberar").status_code == 404

    def test_id_de_reserva_na_rota_de_bloqueios_responde_404(self, cliente: TestClient) -> None:
        cadastrar(cliente)
        reserva_id = cliente.post(
            "/produtos/NB-001/reservas", json={"quantidade": 1, "solicitacao_id": "PED-1"}
        ).json()["id"]

        assert cliente.get(f"/bloqueios/{reserva_id}").status_code == 404
        assert cliente.post(f"/bloqueios/{reserva_id}/liberar").status_code == 404

    def test_consultar_reserva_e_bloqueio_pelo_id(self, cliente: TestClient) -> None:
        cadastrar(cliente)
        reserva = cliente.post(
            "/produtos/NB-001/reservas", json={"quantidade": 1, "solicitacao_id": "PED-1"}
        ).json()
        bloqueio = cliente.post(
            "/produtos/NB-001/bloqueios", json={"quantidade": 1, "solicitacao_id": "INS-1"}
        ).json()

        assert cliente.get(f"/reservas/{reserva['id']}").json() == reserva
        assert cliente.get(f"/bloqueios/{bloqueio['id']}").json() == bloqueio

    def test_saude(self, cliente: TestClient) -> None:
        assert cliente.get("/saude").json() == {"status": "ok"}


class TestValidacaoDeEntrada:
    @pytest.mark.parametrize(
        "corpo",
        [
            {"quantidade": 0},
            {"quantidade": -5},
            {"quantidade": 1_000_001},
            {"quantidade": "3"},  # texto não é convertido
            {"quantidade": 3.0},  # decimal não é aceito
            {"quantidade": True},
            {"quantidade": None},
            {},
            {"quantidae": 3},  # erro de digitação no nome do campo
            {"quantidade": 3, "campo_extra": 1},
        ],
    )
    def test_corpo_invalido_responde_422_sem_alterar_o_estoque(
        self, cliente: TestClient, corpo: dict[str, object]
    ) -> None:
        cadastrar(cliente)
        resposta = cliente.post("/produtos/NB-001/entradas", json=corpo)

        assert resposta.status_code == 422
        assert quantidades(cliente)["quantidade_total"] == 10

    @pytest.mark.parametrize("sku", ["", "NB 001", "-NB", "X" * 51, "NB@001"])
    def test_sku_invalido_no_cadastro_responde_422(self, cliente: TestClient, sku: str) -> None:
        resposta = cliente.post("/produtos", json={"sku": sku, "nome": "Notebook X"})
        assert resposta.status_code == 422

    def test_nome_so_com_espacos_e_barrado_pelo_dominio(self, cliente: TestClient) -> None:
        """O Pydantic aceita "   " (tem tamanho 3), mas o domínio rejeita: defesa em camadas."""
        resposta = cliente.post("/produtos", json={"sku": "NB-001", "nome": "   "})

        assert resposta.status_code == 422
        assert resposta.json()["codigo"] == "DADO_INVALIDO"

    def test_reserva_exige_solicitacao_id(self, cliente: TestClient) -> None:
        cadastrar(cliente)
        resposta = cliente.post("/produtos/NB-001/reservas", json={"quantidade": 1})
        assert resposta.status_code == 422


def test_documentacao_openapi_disponivel(cliente: TestClient) -> None:
    assert cliente.get("/docs").status_code == 200
    rotas = cliente.get("/openapi.json").json()["paths"]
    assert "/produtos/{sku}/reservas" in rotas
    assert "/bloqueios/{bloqueio_id}/liberar" in rotas

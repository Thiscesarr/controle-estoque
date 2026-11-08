"""Teste de concorrência pela API real, via HTTP (só biblioteca padrão do Python).

Dispara requisições SIMULTÂNEAS contra a API rodando e confere se o estoque continua
consistente. Complementa os testes automatizados, que exercitam a concorrência no serviço:
aqui o caminho é o completo (HTTP → FastAPI → pool de threads → SQLite).

Uso, com a API no ar em outro terminal:

    python scripts/estresse.py
    python scripts/estresse.py --pedidos 300 --estoque 50
    python scripts/estresse.py --url http://localhost:8000

Cada execução cria produtos novos (SKU com data/hora), então pode rodar várias vezes.
"""

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "http://localhost:8000"
falhas: list[str] = []


def chamar(metodo: str, rota: str, corpo: dict | None = None) -> tuple[int, dict, float]:
    """Faz a requisição e devolve (status, json, segundos)."""
    dados = None if corpo is None else json.dumps(corpo).encode()
    req = urllib.request.Request(
        URL + rota, data=dados, method=metodo, headers={"Content-Type": "application/json"}
    )
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), time.perf_counter() - inicio
    except urllib.error.HTTPError as erro:
        return erro.code, json.loads(erro.read() or b"{}"), time.perf_counter() - inicio


def verificar(condicao: bool, descricao: str) -> None:
    print(f"  [{'OK ' if condicao else 'FALHOU'}] {descricao}")
    if not condicao:
        falhas.append(descricao)


def disparar(tarefas: list) -> list[tuple[int, dict, float]]:
    """Executa todas as tarefas ao mesmo tempo, uma thread para cada."""
    with ThreadPoolExecutor(max_workers=len(tarefas)) as executor:
        return list(executor.map(lambda tarefa: tarefa(), tarefas))


def resumo_tempos(resultados: list[tuple[int, dict, float]]) -> str:
    tempos = sorted(r[2] * 1000 for r in resultados)
    p95 = tempos[int(len(tempos) * 0.95) - 1]
    return f"média {statistics.mean(tempos):.0f} ms | p95 {p95:.0f} ms | máx {tempos[-1]:.0f} ms"


def contar_status(resultados: list[tuple[int, dict, float]]) -> dict[int, int]:
    contagem: dict[int, int] = {}
    for status, _, _ in resultados:
        contagem[status] = contagem.get(status, 0) + 1
    return dict(sorted(contagem.items()))


def novo_produto(sufixo: str, estoque: int) -> str:
    sku = f"QA-{time.strftime('%H%M%S')}-{sufixo}"
    chamar("POST", "/produtos", {"sku": sku, "nome": f"Produto de estresse {sufixo}"})
    if estoque:
        chamar("POST", f"/produtos/{sku}/entradas", {"quantidade": estoque})
    return sku


def cenario_reservas(pedidos: int, estoque: int) -> None:
    print(f"\n1) {pedidos} reservas simultâneas de 1 unidade, com {estoque} em estoque")
    sku = novo_produto("A", estoque)
    tarefas = [
        lambda n=n: chamar(
            "POST", f"/produtos/{sku}/reservas", {"quantidade": 1, "solicitacao_id": f"P{n}"}
        )
        for n in range(pedidos)
    ]
    resultados = disparar(tarefas)
    status = contar_status(resultados)
    print(f"  status recebidos: {status} | {resumo_tempos(resultados)}")
    aceitas = status.get(201, 0)
    _, situacao, _ = chamar("GET", f"/produtos/{sku}")
    verificar(not any(s >= 500 for s in status), "nenhum erro 5xx")
    verificar(
        aceitas == min(pedidos, estoque), f"aceitas = {min(pedidos, estoque)} (foram {aceitas})"
    )
    verificar(status.get(409, 0) == pedidos - aceitas, "todas as recusadas foram 409")
    verificar(situacao["quantidade_reservada"] == aceitas, "reservado = reservas aceitas")
    verificar(situacao["quantidade_disponivel"] == estoque - aceitas, "disponível correto")


def cenario_misto(operacoes: int, estoque: int) -> None:
    print(f"\n2) {operacoes} operações mistas simultâneas (saída/reserva/bloqueio/entrada)")
    sku = novo_produto("B", estoque)
    sorteio = random.Random(7)
    planos = [
        (sorteio.choice(["saidas", "reservas", "bloqueios", "entradas"]), sorteio.randint(1, 5))
        for _ in range(operacoes)
    ]

    def tarefa(rota: str, qtd: int, n: int):
        corpo = {"quantidade": qtd}
        if rota in ("reservas", "bloqueios"):
            corpo["solicitacao_id"] = f"S{n}"
        return lambda: chamar("POST", f"/produtos/{sku}/{rota}", corpo)

    resultados = disparar([tarefa(r, q, n) for n, (r, q) in enumerate(planos)])
    status = contar_status(resultados)
    print(f"  status recebidos: {status} | {resumo_tempos(resultados)}")

    def soma(rota: str) -> int:
        return sum(
            q
            for (r, q), (s, _, _) in zip(planos, resultados, strict=True)
            if r == rota and s in (200, 201)
        )

    _, sit, _ = chamar("GET", f"/produtos/{sku}")
    _, historico, _ = chamar("GET", f"/produtos/{sku}/movimentacoes")
    sucesso = sum(1 for s, _, _ in resultados if s in (200, 201))
    verificar(not any(s >= 500 for s in status), "nenhum erro 5xx")
    verificar(
        sit["quantidade_total"] == estoque + soma("entradas") - soma("saidas"),
        "total = estoque inicial + entradas - saídas aceitas",
    )
    verificar(sit["quantidade_reservada"] == soma("reservas"), "reservado = reservas aceitas")
    verificar(sit["quantidade_bloqueada"] == soma("bloqueios"), "bloqueado = bloqueios aceitos")
    verificar(sit["quantidade_disponivel"] >= 0, "disponível nunca negativo")
    verificar(len(historico) == 1 + sucesso, "uma movimentação por operação aceita (+1 entrada)")


def cenario_liberacao_dupla(tentativas: int) -> None:
    print(f"\n3) a mesma reserva liberada {tentativas} vezes ao mesmo tempo")
    sku = novo_produto("C", 10)
    _, reserva, _ = chamar(
        "POST", f"/produtos/{sku}/reservas", {"quantidade": 4, "solicitacao_id": "P1"}
    )
    resultados = disparar(
        [lambda: chamar("POST", f"/reservas/{reserva['id']}/liberar") for _ in range(tentativas)]
    )
    status = contar_status(resultados)
    print(f"  status recebidos: {status}")
    _, sit, _ = chamar("GET", f"/produtos/{sku}")
    verificar(status.get(200, 0) == 1, "exatamente 1 liberação aceita")
    verificar(status.get(409, 0) == tentativas - 1, "as demais receberam 409")
    verificar(sit["quantidade_disponivel"] == 10, "disponível voltou a 10 (e não mais)")


def cenario_liberar_contra_efetivar(tentativas: int) -> None:
    print(f"\n4) a mesma reserva: {tentativas} liberações e {tentativas} efetivações juntas")
    sku = novo_produto("D", 10)
    _, reserva, _ = chamar(
        "POST", f"/produtos/{sku}/reservas", {"quantidade": 4, "solicitacao_id": "P1"}
    )
    acoes = ["liberar"] * tentativas + ["efetivar"] * tentativas
    resultados = disparar(
        [lambda acao=acao: chamar("POST", f"/reservas/{reserva['id']}/{acao}") for acao in acoes]
    )
    vencedoras = [a for a, (s, _, _) in zip(acoes, resultados, strict=True) if s == 200]
    print(f"  status recebidos: {contar_status(resultados)} | venceu: {vencedoras}")
    _, sit, _ = chamar("GET", f"/produtos/{sku}")
    verificar(len(vencedoras) == 1, "exatamente 1 operação aceita")
    esperado = 10 if vencedoras == ["liberar"] else 6
    verificar(sit["quantidade_total"] == esperado, f"total coerente com quem venceu ({esperado})")
    verificar(sit["quantidade_reservada"] == 0, "reservado zerado")


def main() -> None:
    global URL
    parser = argparse.ArgumentParser(description="Teste de estresse da API de estoque")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--pedidos", type=int, default=100)
    parser.add_argument("--estoque", type=int, default=30)
    args = parser.parse_args()
    URL = args.url.rstrip("/")

    try:
        chamar("GET", "/saude")
    except urllib.error.URLError:
        raise SystemExit(f"API fora do ar em {URL}. Suba com: uvicorn estoque.main:app") from None

    inicio = time.perf_counter()
    cenario_reservas(args.pedidos, args.estoque)
    cenario_misto(args.pedidos, args.estoque)
    cenario_liberacao_dupla(20)
    cenario_liberar_contra_efetivar(10)
    print(f"\nTempo total: {time.perf_counter() - inicio:.1f} s")
    print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S): {falhas}")


if __name__ == "__main__":
    main()

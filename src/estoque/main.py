"""Ponto de entrada do servidor.

Uso: `uvicorn estoque.main:app --reload` e abra http://localhost:8000/docs
"""

from estoque.api.app import criar_app

app = criar_app()

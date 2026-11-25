import unittest
from datetime import date, timedelta
from unittest.mock import patch

import app


class ClientesNovosTestes(unittest.TestCase):
    def test_periodos_e_primeira_compra_consolidada(self):
        hoje = date(2026, 11, 24)
        idades = [-1, 0, 6, 7, 14, 15, 29, 30]
        clientes = [dict(id=str(i), nome=f"Pessoa[{i}]") for i in idades]
        clientes += [dict(id="antigo", nome="Antigo", celular="38999999999"),
                     dict(id="duplicado", telefone="38999999999"),
                     dict(id="sem", nome="Sem compras")]
        vendas = [dict(valor_total="50", cliente_id=str(i), data=(hoje-timedelta(days=i)).isoformat())
                  for i in idades]
        vendas += [dict(valor_total="50", cliente_id="duplicado", data=hoje.isoformat()),
                   dict(valor_total="50", cliente_id="antigo", data="2025-12-10"),
                   dict(valor_total="50", cliente_id="0", data="2025-12-01", cancelado="sim")]
        with patch.object(app, "listar_todos", return_value=clientes), patch.object(app, "listar_vendas", return_value=vendas):
            base = app.gerar_base_clientes()
        config = {"SMARTONE_ADMIN_USER": "teste", "SMARTONE_ADMIN_PASSWORD": "senha-local-para-testes"}
        with patch.dict(app.app.config, config), patch.object(app, "carregar_clientes", return_value=base), patch.object(app, "hoje_local", return_value=hoje), patch.object(app, "carregar_aniversarios_locais", return_value=({}, None)):
            client = app.app.test_client()
            for dias in [7, 15, 30]:
                resposta = client.get(f"/clientes-novos?dias={dias}", auth=("teste", config["SMARTONE_ADMIN_PASSWORD"]))
                self.assertEqual(resposta.status_code, 200)
                html = resposta.get_data(as_text=True)
                for i in idades:
                    self.assertEqual(f"Pessoa[{i}]" in html, 0 <= i < dias)
                self.assertNotIn("Antigo", html)
                self.assertNotIn("Sem compras", html)
                self.assertIn("Primeira compra", html)

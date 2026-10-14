"""Testes de segurança sem acessar o GestãoClick nem dados reais."""
import unittest
from unittest.mock import patch

import app as modulo


class SegurancaTestes(unittest.TestCase):
    def setUp(self):
        self.usuario = "admin"
        self.senha = "senha-de-teste-mais-de-vinte-caracteres"
        modulo.app.config.update(
            TESTING=True,
            SMARTONE_ADMIN_USER=self.usuario,
            SMARTONE_ADMIN_PASSWORD=self.senha,
        )
        self.cliente = modulo.app.test_client()

    def autorizacao(self, usuario=None, senha=None):
        from base64 import b64encode
        dados = f"{usuario or self.usuario}:{senha or self.senha}".encode()
        return {"Authorization": "Basic " + b64encode(dados).decode()}

    def test_health_publico_sem_dados(self):
        resposta = self.cliente.get("/health")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.get_json(), {"status": "ok", "app": "Smart One Clientes"})

    def test_painel_exige_autenticacao_sem_consultar_api(self):
        with patch.object(modulo, "carregar_clientes", side_effect=AssertionError("Consulta indevida")):
            resposta = self.cliente.get("/")
            self.assertEqual(resposta.status_code, 401)
            self.assertIn("Basic", resposta.headers["WWW-Authenticate"])
            resposta_head = self.cliente.head("/")
            self.assertEqual(resposta_head.status_code, 401)

    def test_credenciais_incorretas_bloqueadas(self):
        resposta = self.cliente.get("/", headers=self.autorizacao(senha="incorreta"))
        self.assertEqual(resposta.status_code, 401)

    def test_sem_senha_forte_bloqueia_mesmo_login_correto(self):
        modulo.app.config["SMARTONE_ADMIN_PASSWORD"] = "curta"
        resposta = self.cliente.get("/", headers=self.autorizacao(senha="curta"))
        self.assertEqual(resposta.status_code, 503)

    def test_rotas_de_diagnostico_nao_existem(self):
        rotas_antigas = (
            "/teste-gestaoclick", "/teste-vendas", "/debug-clientes",
            "/debug-vendas", "/teste-clientes-vendas",
        )
        for rota in rotas_antigas:
            with self.subTest(rota=rota):
                resposta = self.cliente.get(rota, headers=self.autorizacao())
                self.assertEqual(resposta.status_code, 404)


    def test_cartoes_filtram_clientes(self):
        html = modulo.HTML
        filtros = {
            "Clientes cadastrados": "",
            "Clientes ativos": "Ativo",
            "VIPs": "VIP",
            "Clientes em risco": "Em risco",
            "Clientes inativos": "Inativo",
            "VIPs em risco/inativos": "vip_risco_inativo",
        }
        for titulo, filtro in filtros.items():
            with self.subTest(cartao=titulo):
                self.assertIn("abrirClientes('" + filtro + "')", html)
                self.assertIn(titulo, html)
        self.assertEqual(html.count('class="card filtro-card"'), 5)
        self.assertIn('id="secaoClientes"', html)
        self.assertIn('status_atual == "vip_risco_inativo"', html)
        self.assertIn('irParaLista(status, "", 1)', html)
        self.assertEqual(html.count("</button>"), 6)


    def test_dashboard_pagina_apenas_80_clientes(self):
        clientes = [{
            "id": str(i), "nome": f"Cliente {i}", "celular": "38999999999",
            "status": "Inativo" if i % 2 == 0 else "Ativo",
            "vip": False, "total_gasto": float(i), "compras": 1,
            "aniversario_hoje": False, "dias_ate_aniversario": None,
            "ultima_compra_formatada": "01/01/2026",
            "total_gasto_formatado": "R$ 1,00",
            "ticket_medio_formatado": "R$ 1,00",
        } for i in range(200)]
        with patch.object(modulo, "carregar_clientes", return_value=clientes):
            resposta = self.cliente.get("/", headers=self.autorizacao())
            self.assertEqual(resposta.status_code, 200)
            html = resposta.get_data(as_text=True)
            self.assertEqual(html.count("<tr data-status="), 80)
            self.assertIn("Mostrando 1–80 de 200 clientes", html)
            resposta2 = self.cliente.get("/?status=Inativo&pagina=2", headers=self.autorizacao())
            self.assertEqual(resposta2.status_code, 200)
            pagina2 = resposta2.get_data(as_text=True)
            self.assertEqual(pagina2.count("<tr data-status="), 20)
            self.assertIn("Mostrando 81–100 de 100 clientes", pagina2)

    def test_busca_filtra_no_servidor(self):
        registros = [{
            "id": "1", "nome": "Maria Costa", "celular": "38999999999",
            "status": "Em risco", "vip": False, "total_gasto": 12.0,
            "aniversario_hoje": False, "dias_ate_aniversario": None,
            "compras": 1, "ultima_compra_formatada": "-",
            "total_gasto_formatado": "R$ 12,00",
            "ticket_medio_formatado": "R$ 12,00",
        }]
        with patch.object(modulo, "carregar_clientes", return_value=registros):
            response = self.cliente.get("/?busca=maria&status=Em%20risco", headers=self.autorizacao())
            self.assertEqual(response.status_code, 200)
            self.assertIn("Mostrando 1–1 de 1 clientes", response.get_data(as_text=True))
            response = self.cliente.get("/?busca=inexistente", headers=self.autorizacao())
            self.assertEqual(response.status_code, 200)
            self.assertIn("Mostrando 0–0 de 0 clientes", response.get_data(as_text=True))


    def test_aniversarios_exigem_login(self):
        self.assertEqual(self.cliente.get("/aniversarios").status_code, 401)
        self.assertEqual(self.cliente.post("/aniversarios/salvar").status_code, 401)

    def test_aniversarios_sem_banco_nao_permite_gravacao(self):
        cliente = {
            "id": "123", "nome": "Maria Silva", "celular": "38999999999",
            "data_nascimento": None, "status": "Ativo", "vip": False,
            "aniversario_hoje": False, "dias_ate_aniversario": None,
        }
        with patch.object(modulo, "carregar_clientes", return_value=[cliente]), \
             patch.object(modulo, "banco_aniversarios_configurado", return_value=False):
            resposta = self.cliente.get("/aniversarios?busca=Maria", headers=self.autorizacao())
            self.assertEqual(resposta.status_code, 200)
            html = resposta.get_data(as_text=True)
            self.assertIn("Maria Silva", html)
            self.assertIn("cadastro de aniversários está desativado", html)
            token = modulo.URLSafeTimedSerializer(
                self.senha, salt="smartone-aniversarios-form-v1"
            ).dumps(self.usuario)
            resposta = self.cliente.post(
                "/aniversarios/salvar",
                data={"csrf_token": token, "cliente_id": "123", "data_nascimento": "2000-04-10"},
                headers=self.autorizacao(),
            )
            self.assertEqual(resposta.status_code, 503)

    def test_aniversarios_validam_token_e_cliente(self):
        with patch.object(modulo, "banco_aniversarios_configurado", return_value=True):
            r = self.cliente.post(
                "/aniversarios/salvar",
                data={"csrf_token": "token-falso", "cliente_id": "123", "data_nascimento": "2000-04-10"},
                headers=self.autorizacao(),
            )
            self.assertEqual(r.status_code, 403)
        token = modulo.URLSafeTimedSerializer(
            self.senha, salt="smartone-aniversarios-form-v1"
        ).dumps(self.usuario)
        with patch.object(modulo, "banco_aniversarios_configurado", return_value=True), \
             patch.object(modulo, "carregar_clientes", return_value=[{"id": "123"}]), \
             patch.object(modulo, "salvar_aniversario") as salvar:
            r = self.cliente.post(
                "/aniversarios/salvar",
                data={"csrf_token": token, "cliente_id": "123", "data_nascimento": "2000-04-10"},
                headers=self.autorizacao(),
            )
            self.assertEqual(r.status_code, 303)
            salvar.assert_called_once()
            r = self.cliente.post(
                "/aniversarios/salvar",
                data={"csrf_token": token, "cliente_id": "outro", "data_nascimento": "2000-04-10"},
                headers=self.autorizacao(),
            )
            self.assertEqual(r.status_code, 404)

    def test_aniversarios_lista_clientes_sem_data_primeiro(self):
        from unittest.mock import patch
        registros = [
            {"id": str(i), "nome": f"Cliente {i:03d}",
             "celular": "38999999999",
             "data_nascimento": "1995-10-11" if i % 2 else None}
            for i in range(140)
        ]
        with patch.object(modulo, "carregar_clientes", return_value=registros), \
             patch.object(modulo, "carregar_aniversarios_locais", return_value=({}, None)):
            resposta = self.cliente.get("/aniversarios", headers=self.autorizacao())
            self.assertEqual(resposta.status_code, 200)
            pagina = resposta.get_data(as_text=True)
            self.assertIn("Mostrando 1–60 de 140 clientes", pagina)
            self.assertEqual(pagina.count('action="/aniversarios/salvar"'), 60)
            self.assertIn("Cliente 000", pagina)
            self.assertNotIn("Cliente 001", pagina)
            self.assertIn("Próxima →", pagina)
            segunda = self.cliente.get("/aniversarios?pagina=2", headers=self.autorizacao())
            self.assertIn("Mostrando 61–120 de 140 clientes", segunda.get_data(as_text=True))
            ultima = self.cliente.get("/aniversarios?pagina=3", headers=self.autorizacao())
            self.assertEqual(ultima.get_data(as_text=True).count('action="/aniversarios/salvar"'), 20)

    def test_cabecalhos_restringem_cache(self):
        resposta = self.cliente.get("/health")
        self.assertIn("no-store", resposta.headers["Cache-Control"])
        self.assertEqual(resposta.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")


if __name__ == "__main__":
    unittest.main()

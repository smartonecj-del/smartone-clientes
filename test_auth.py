import base64
import unittest
from unittest.mock import patch
import app as modulo


class AcessoTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(modulo.app.config, {
            "SMARTONE_ADMIN_USER": "admin", "SMARTONE_ADMIN_PASSWORD": "credencial-ficticia-para-testes"
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        self.client = modulo.app.test_client()

    def cabecalho(self, senha="credencial-ficticia-para-testes"):
        valor = base64.b64encode(("admin:" + senha).encode()).decode()
        return {"Authorization": "Basic " + valor}

    def test_anonimo_nao_consulta_erp_nem_acessa_base(self):
        with patch.object(modulo, "consultar_api") as api, patch.object(modulo, "carregar_clientes") as base:
            for rota in ("/", "/teste-gestaoclick", "/teste-vendas", "/debug-clientes",
                         "/debug-vendas", "/teste-clientes-vendas", "/rotas"):
                with self.subTest(rota=rota):
                    r = self.client.get(rota)
                    self.assertEqual(r.status_code, 401)
                    self.assertIn("Basic", r.headers["WWW-Authenticate"])
            api.assert_not_called()
            base.assert_not_called()

    def test_sem_senha_ou_senha_curta_fica_fechado(self):
        for senha in ("", "curta"):
            with patch.dict(modulo.app.config, {"SMARTONE_ADMIN_PASSWORD": senha}), \
                 patch.object(modulo, "carregar_clientes") as base:
                self.assertEqual(self.client.get("/").status_code, 503)
                self.assertEqual(self.client.get("/teste-clientes-vendas").status_code, 503)
                base.assert_not_called()

    def test_senha_errada_e_token_na_url_nao_autenticam(self):
        self.assertEqual(self.client.get("/rotas", headers=self.cabecalho("errada")).status_code, 401)
        self.assertEqual(self.client.get("/rotas?password=credencial-ficticia-para-testes").status_code, 401)
        self.assertEqual(self.client.get("/rotas", headers={"Authorization": "Basic invalido"}).status_code, 401)

    def test_acesso_valido_usa_cabecalho_e_nao_cache_publico(self):
        r = self.client.get("/rotas", headers=self.cabecalho())
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r.headers["Cache-Control"])
        self.assertIn("Authorization", r.headers["Vary"])
        self.assertIn("noindex", r.headers["X-Robots-Tag"])

    def test_health_publico_head_sem_dados(self):
        with patch.object(modulo, "carregar_clientes") as base:
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.head("/").status_code, 200)
            self.assertEqual(self.client.head("/").data, b"")
            base.assert_not_called()

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
        self.assertIn('status === "vip_risco_inativo"', html)
        self.assertIn('scrollIntoView', html)
        self.assertEqual(html.count("</button>"), 6)

    def test_cabecalhos_restringem_cache(self):
        resposta = self.cliente.get("/health")
        self.assertIn("no-store", resposta.headers["Cache-Control"])
        self.assertEqual(resposta.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")


if __name__ == "__main__":
    unittest.main()

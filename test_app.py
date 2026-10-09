"""Regressões offline: nenhum token real e nenhuma escrita no GestãoClick."""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
import app as modulo


HOJE = date(2026, 10, 8)


class PaginacaoTests(unittest.TestCase):
    def consultar(self, respostas, filtros=None):
        with patch.object(modulo, "consultar_api", side_effect=respostas) as consulta:
            resultado = modulo.listar_todos("clientes", filtros)
        return resultado, consulta

    def test_data_com_proxima_url_e_filtros(self):
        filtros = {"nome": "Teste"}
        registros, consulta = self.consultar([
            {"data": [{"id": "1"}], "meta": {"proxima_url": "/api/clientes?pagina=2"}},
            {"data": [{"id": "2"}], "meta": {"proxima_url": None, "total_registros": 2}},
        ], filtros)
        self.assertEqual([r["id"] for r in registros], ["1", "2"])
        self.assertEqual(consulta.call_args_list[1].args[1], {
            "nome": "Teste", "pagina": 2, "limite": 100
        })
        self.assertEqual(filtros, {"nome": "Teste"})

    def test_dados_sem_meta_nao_para_em_pagina_curta(self):
        registros, consulta = self.consultar([
            {"dados": [{"id": 1}]}, {"dados": [{"id": 2}]}, {"dados": []}
        ])
        self.assertEqual(len(registros), 2)
        self.assertEqual(consulta.call_count, 3)

    def test_proxima_pagina_numerica(self):
        registros, consulta = self.consultar([
            {"dados": [{"id": 1}], "meta": {"proxima_pagina": "2"}},
            {"dados": [{"id": 2}], "meta": {"proxima_pagina": None}},
        ])
        self.assertEqual(len(registros), 2)
        self.assertEqual(consulta.call_args_list[1].args[1]["pagina"], 2)

    def test_total_paginas(self):
        registros, consulta = self.consultar([
            {"data": [{"id": 1}], "meta": {"total_paginas": 2}},
            {"data": [{"id": 2}], "meta": {"total_paginas": 2}},
        ])
        self.assertEqual(len(registros), 2)
        self.assertEqual(consulta.call_count, 2)

    def test_total_registros(self):
        registros, _ = self.consultar([
            {"data": [{"id": 1}], "meta": {"total_registros": 2}},
            {"data": [{"id": 2}], "meta": {"total_registros": 2}},
        ])
        self.assertEqual(len(registros), 2)

    def test_pagina_repetida_falha_sem_resultado_parcial(self):
        with self.assertRaisesRegex(RuntimeError, "repetiu"):
            self.consultar([{"data": [{"id": 1}]}] * 2)

    def test_proxima_pagina_regressiva_falha(self):
        with self.assertRaisesRegex(RuntimeError, "Próxima"):
            self.consultar([{"data": [{"id": 1}], "meta": {"proxima_pagina": 1}}])

    def test_sobreposicao_nao_duplica_ids(self):
        registros, _ = self.consultar([
            {"data": [{"id": 1}, {"id": 2}]},
            {"data": [{"id": "2"}, {"id": 3}]},
            {"data": []},
        ])
        self.assertEqual(len(registros), 3)

    def test_payload_invalido_nao_vira_lista_vazia(self):
        for payload in ({}, {"data": None}, {"data": {}},
                        {"data": [1]}, {"code": 401, "data": []},
                        {"status": "error", "data": []}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.consultar([payload])

    def test_total_incompleto_falha(self):
        with self.assertRaisesRegex(RuntimeError, "incompleta"):
            self.consultar([{
                "data": [{"id": 1}],
                "meta": {"total_registros": 2, "proxima_url": None},
            }])

    def test_pagina_vazia_antes_do_fim_falha(self):
        with self.assertRaisesRegex(RuntimeError, "antes do fim"):
            self.consultar([{"data": [], "meta": {"total_paginas": 2}}])

    def test_falha_na_segunda_pagina_nao_retorna_primeira(self):
        with self.assertRaises(requests.Timeout):
            self.consultar([{"data": [{"id": 1}]}, requests.Timeout("timeout")])


class IntegracaoTests(unittest.TestCase):
    def test_headers_timeout_filtros_e_get(self):
        resposta = Mock()
        resposta.json.return_value = {"data": []}
        with patch.object(modulo, "ACCESS_TOKEN", "token-ficticio"), \
             patch.object(modulo, "SECRET_ACCESS_TOKEN", "segredo-ficticio"), \
             patch.object(modulo.requests, "get", return_value=resposta) as get:
            self.assertEqual(modulo.consultar_api("clientes", {"pagina": 1}), {"data": []})
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/clientes"))
        self.assertEqual(kwargs["headers"]["access-token"], "token-ficticio")
        self.assertEqual(kwargs["headers"]["secret-access-token"], "segredo-ficticio")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["params"], {"pagina": 1})
        resposta.raise_for_status.assert_called_once()

    def test_sem_credenciais_nao_faz_requisicao(self):
        with patch.object(modulo, "ACCESS_TOKEN", None), \
             patch.object(modulo.requests, "get") as get:
            with self.assertRaisesRegex(RuntimeError, "Credenciais"):
                modulo.consultar_api("clientes")
            get.assert_not_called()

    def test_http_erro_e_json_invalido_sao_propagados(self):
        for erro in (requests.HTTPError("401"), ValueError("JSON inválido")):
            resposta = Mock()
            if isinstance(erro, requests.HTTPError):
                resposta.raise_for_status.side_effect = erro
            else:
                resposta.json.side_effect = erro
            with self.subTest(erro=erro), \
                 patch.object(modulo, "ACCESS_TOKEN", "teste"), \
                 patch.object(modulo, "SECRET_ACCESS_TOKEN", "teste"), \
                 patch.object(modulo.requests, "get", return_value=resposta), \
                 self.assertRaises(type(erro)):
                modulo.consultar_api("clientes")


class IndicadoresTests(unittest.TestCase):
    def base(self):
        clientes = [
            {"id": 1, "nome": "Cliente teste", "telefone": "38999990000",
             "data_nascimento": "1990-10-08"},
            {"id": "2", "nome": "Sem compras"},
        ]
        vendas = [
            {"id": 10, "cliente_id": "1", "valor_total": "100.10", "data": "2026-07-01"},
            {"id": 11, "cliente_id": 1, "data": "2026-10-07T14:00:00",
             "pagamentos": [
                 {"pagamento": {"valor": "50.00"}},
                 {"pagamento": {"valor": "50,20"}},
             ]},
            {"id": 11, "cliente_id": 1, "data": "2026-10-07", "valor_total": "100.20"},
            {"id": 12, "cliente_id": 1, "data": "2026-10-08",
             "valor_total": "999", "nome_situacao": "Cancelada"},
            {"id": 13, "cliente_id": None, "valor_total": "999"},
        ]
        with patch.object(modulo, "listar_todos", side_effect=[clientes, vendas, [], []]), \
             patch.object(modulo, "hoje_local", return_value=HOJE):
            return modulo.gerar_base_clientes()

    def test_compras_total_ticket_ultima_e_sem_compra(self):
        cliente, sem_compra = self.base()
        self.assertEqual(cliente["compras"], 2)
        self.assertEqual(cliente["total_gasto"], 200.30)
        self.assertEqual(cliente["ticket_medio"], 100.15)
        self.assertEqual(cliente["ultima_compra"], "2026-10-07")
        self.assertEqual(cliente["ultima_compra_formatada"], "07/10/2026")
        self.assertEqual(cliente["dias_sem_comprar"], 1)
        self.assertEqual(cliente["status"], "Ativo")
        self.assertTrue(cliente["aniversario_hoje"])
        self.assertEqual(cliente["celular"], "38999990000")
        self.assertEqual(sem_compra["compras"], 0)
        self.assertEqual(sem_compra["ticket_medio"], 0)
        self.assertIsNone(sem_compra["ultima_compra"])
        self.assertEqual(sem_compra["status"], "Sem compra")

    def test_limites_status_preservados(self):
        with patch.object(modulo, "hoje_local", return_value=HOJE):
            for dias, esperado in ((0, "Ativo"), (89, "Ativo"), (90, "Em risco"),
                                   (179, "Em risco"), (180, "Inativo")):
                with self.subTest(dias=dias):
                    self.assertEqual(modulo.definir_status(
                        (HOJE - timedelta(days=dias)).isoformat()), esperado)
            self.assertEqual(modulo.definir_status(None), "Sem compra")

    def test_total_prioritario_nao_soma_total_e_parcelas(self):
        self.assertEqual(modulo.total_da_venda({
            "valor_total": "100", "pagamentos": [{"pagamento": {"valor": "100"}}]
        }), Decimal("100"))
        self.assertEqual(modulo.total_da_venda({"valor_total": 0}), Decimal("0"))

    def test_decimal_brasileiro_e_valores_invalidos(self):
        self.assertEqual(modulo.valor_decimal("R$ 1.234,56"), Decimal("1234.56"))
        self.assertEqual(modulo.valor_decimal("1234.56"), Decimal("1234.56"))
        for valor in ("abc", "NaN", "Infinity", None):
            with self.subTest(valor=valor), self.assertRaises(RuntimeError):
                modulo.valor_decimal(valor)
        with self.assertRaises(RuntimeError):
            modulo.total_da_venda({})

    def test_vip_exige_os_dois_limites(self):
        self.assertTrue(modulo.calcular_vip(1000, 5))
        self.assertFalse(modulo.calcular_vip(999, 5))
        self.assertFalse(modulo.calcular_vip(1000, 4))

    def test_datas_iso_brasileira_e_invalida(self):
        for entrada in ("2026-10-08", "2026-10-08T12:30:00Z", "08/10/2026", HOJE):
            with self.subTest(entrada=entrada):
                self.assertEqual(modulo.converter_data(entrada), HOJE)
        self.assertIsNone(modulo.converter_data("inválida"))
        self.assertIsNone(modulo.converter_data(None))

    def test_venda_com_data_invalida_falha(self):
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": 1}], [{"cliente_id": 1, "valor_total": 1, "data": "inválida"}], [], []
        ]), self.assertRaisesRegex(RuntimeError, "data"):
            modulo.gerar_base_clientes()

    def test_paginacao_clientes_e_vendas_integrada(self):
        respostas = [
            {"dados": [{"id": 1, "nome": "A"}], "meta": {"proxima_pagina": 2}},
            {"dados": [{"id": 2, "nome": "B"}], "meta": {"proxima_pagina": None}},
            {"data": [{"id": 10, "cliente_id": 1, "valor_total": "100", "data": "2026-10-01"}],
             "meta": {"proxima_url": "/api/vendas?pagina=2"}},
            {"data": [{"id": 11, "cliente_id": 1, "valor_total": "300", "data": "2026-10-08"}],
             "meta": {"proxima_url": None}},
            {"data": []},
            {"data": []},
        ]
        with patch.object(modulo, "consultar_api", side_effect=respostas) as consulta, \
             patch.object(modulo, "hoje_local", return_value=HOJE):
            base = modulo.gerar_base_clientes()
        self.assertEqual(base[0]["compras"], 2)
        self.assertEqual(base[0]["total_gasto"], 400)
        self.assertEqual(base[0]["ticket_medio"], 200)
        self.assertEqual(base[0]["ultima_compra"], "2026-10-08")
        self.assertEqual([c.args[0] for c in consulta.call_args_list],
                         ["clientes", "clientes", "vendas", "vendas", "vendas", "vendas"])

    def test_balcao_entra_no_historico_sem_duplicar_outros_tipos(self):
        venda = {"id": 10, "cliente_id": 1, "valor_total": "23.90", "data": "2026-10-08"}
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": 1, "nome": "Cliente de teste"}], [venda], [],
            [dict(venda, id="10"), dict(venda, id=11)],
        ]) as consulta:
            cliente = modulo.gerar_base_clientes()[0]
        self.assertEqual(cliente["compras"], 2)
        self.assertEqual(cliente["total_gasto"], 47.80)
        self.assertEqual(cliente["ticket_medio"], 23.90)
        self.assertEqual([c.args for c in consulta.call_args_list[1:]], [
            ("vendas", {"tipo": "produto"}),
            ("vendas", {"tipo": "servico"}),
            ("vendas", {"tipo": "vendas_balcao"}),
        ])

    def test_falha_balcao_nao_retorna_historico_parcial(self):
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": 1}], [], [], requests.Timeout("teste")
        ]), self.assertRaises(requests.Timeout):
            modulo.gerar_base_clientes()

    def test_aniversario_29_fevereiro(self):
        with patch.object(modulo, "hoje_local", return_value=date(2027, 2, 27)):
            self.assertEqual(modulo.dias_ate_aniversario("2000-02-29"), 1)


class ConsumidorTests(unittest.TestCase):
    def consolidar(self, clientes, vendas):
        with patch.object(modulo, "listar_todos", return_value=clientes), \
             patch.object(modulo, "listar_vendas", return_value=vendas):
            return modulo.gerar_base_clientes()

    def venda(self, **campos):
        return dict({"id": "10", "cliente_id": "", "nome_cliente": "Ana Teste - 11987654321",
                     "data": "2026-10-08", "valor_total": "23.90"}, **campos)

    def test_avulso_agrupa_compras_e_preserva_telefone(self):
        base = self.consolidar([], [self.venda(), self.venda(id="11", valor_total="10")])
        self.assertEqual(len(base), 1)
        self.assertEqual(base[0]["nome"], "Ana Teste")
        self.assertEqual(base[0]["celular"], "11987654321")
        self.assertEqual(base[0]["compras"], 2)
        self.assertEqual(base[0]["total_gasto"], 33.90)

    def test_vincula_cadastro_unico_com_nome_e_telefone(self):
        base = self.consolidar([{"id": 1, "nome": "ANA TESTE", "celular": "+55 (11) 98765-4321"}],
                              [self.venda(), self.venda(id="11", cliente_id="1")])
        self.assertEqual(len(base), 1)
        self.assertEqual(base[0]["id"], "1")
        self.assertEqual(base[0]["compras"], 2)

    def test_telefone_compartilhado_e_homonimos_nao_misturam(self):
        base = self.consolidar([], [self.venda(), self.venda(id="11", nome_cliente="Bia Teste - 11987654321"),
                                  self.venda(id="12", nome_cliente="Ana Teste - 21987654321")])
        self.assertEqual(len(base), 3)
        self.assertTrue(all(c["compras"] == 1 for c in base))

    def test_cadastros_ambiguos_nao_recebem_venda(self):
        base = self.consolidar([{"id": n, "nome": "Ana Teste", "celular": "11987654321"} for n in (1, 2)],
                              [self.venda()])
        self.assertEqual([c["compras"] for c in base], [0, 0, 1])

    def test_sem_identificacao_ou_cancelada_nao_cria_perfil(self):
        for campos in ({"nome_cliente": "Consumidor final"}, {"nome_cliente": "Ana - 123"},
                       {"nome_situacao": "Cancelada"}):
            with self.subTest(campos=campos):
                self.assertEqual(self.consolidar([], [self.venda(**campos)]), [])


class RotasTests(unittest.TestCase):
    def setUp(self):
        self.client = modulo.app.test_client()

    def test_dashboard_e_json_mesmos_indicadores(self):
        base = IndicadoresTests().base()
        with patch.object(modulo, "gerar_base_clientes", return_value=base):
            painel = self.client.get("/")
            api = self.client.get("/teste-clientes-vendas")
        self.assertEqual(painel.status_code, 200)
        self.assertIn("SMART ONE CLIENTES", painel.text)
        self.assertIn("R$ 200,30", painel.text)
        self.assertIn("R$ 100,15", painel.text)
        self.assertIn("07/10/2026", painel.text)
        self.assertIn('data-status="ativo"', painel.text)
        self.assertEqual(api.status_code, 200)
        cliente = api.json["clientes"][0]
        self.assertEqual(cliente["quantidade_compras"], 2)
        self.assertEqual(cliente["total_gasto"], 200.30)
        self.assertEqual(cliente["ultima_compra"], "2026-10-07")
        self.assertEqual(api.json["quantidade_clientes"], 2)

    def test_falha_api_nao_mostra_indicadores_zerados(self):
        with patch.object(modulo, "gerar_base_clientes", side_effect=requests.Timeout("teste")), \
             patch.object(modulo.app.logger, "exception"):
            self.assertEqual(self.client.get("/").status_code, 500)
            self.assertEqual(self.client.get("/teste-clientes-vendas").status_code, 500)

    def test_health_sem_credenciais(self):
        self.assertEqual(self.client.get("/health").json["status"], "ok")

    def test_head_nao_consulta_erp(self):
        with patch.object(modulo, "gerar_base_clientes") as gerar:
            self.assertEqual(self.client.head("/").status_code, 200)
            gerar.assert_not_called()

    def test_diagnostico_id_interno(self):
        with patch.object(modulo, "consultar_api", return_value={"data": {}}) as consulta:
            self.assertEqual(self.client.get("/teste-vendas?id=404287103").status_code, 200)
            consulta.assert_called_once_with("vendas/404287103", {})
        for query in ("id=abc", "id=1&tipo=vendas_balcao", "id=1&codigo=1759"):
            with self.subTest(query=query), patch.object(modulo, "consultar_api") as consulta:
                self.assertEqual(self.client.get("/teste-vendas?" + query).status_code, 400)
                consulta.assert_not_called()

    def test_diagnosticos_usam_mesma_integracao(self):
        for rota, endpoint, campo in (
            ("/teste-gestaoclick", "clientes", "resposta"),
            ("/teste-vendas", "vendas", "resposta"),
            ("/debug-clientes", "clientes", "resposta_completa"),
            ("/debug-vendas", "vendas", "resposta_completa"),
        ):
            with self.subTest(rota=rota), patch.object(
                modulo, "consultar_api", return_value={"data": []}
            ) as consulta:
                resposta = self.client.get(rota)
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.json[campo], {"data": []})
                consulta.assert_called_once_with(endpoint, {"pagina": 1, "limite": 100})

    def test_diagnostico_http_erro_nao_retorna_sucesso(self):
        resposta_http = requests.Response()
        resposta_http.status_code = 401
        erro = requests.HTTPError(response=resposta_http)
        with patch.object(modulo, "consultar_api", side_effect=erro):
            resposta = self.client.get("/teste-gestaoclick")
        self.assertEqual(resposta.status_code, 502)
        self.assertEqual(resposta.json["status_http"], 401)

    def test_diagnostico_balcao_com_codigo(self):
        with patch.object(modulo, "consultar_api", return_value={"data": []}) as consulta:
            resposta = self.client.get("/teste-vendas?tipo=vendas_balcao&codigo=1759")
        self.assertEqual(resposta.status_code, 200)
        consulta.assert_called_once_with("vendas", {
            "pagina": 1, "limite": 100, "tipo": "vendas_balcao", "codigo": "1759"
        })

    def test_diagnostico_rejeita_filtros_invalidos(self):
        for query in ("tipo=outro", "codigo=abc", "codigo=-1", "codigo=", "codigo=١"):
            with self.subTest(query=query), patch.object(modulo, "consultar_api") as consulta:
                self.assertEqual(self.client.get("/teste-vendas?" + query).status_code, 400)
                consulta.assert_not_called()

    def test_rotas_atuais_preservadas(self):
        rotas = self.client.get("/rotas").json
        for rota in ("/", "/health", "/teste-clientes-vendas", "/teste-gestaoclick",
                     "/teste-vendas", "/debug-clientes", "/debug-vendas"):
            self.assertIn(rota, rotas)


if __name__ == "__main__":
    unittest.main()


"""Regressões offline: nenhum token real e nenhuma escrita no GestãoClick."""
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
import app as modulo
import validar_balcao as diagnostico


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
    def setUp(self):
        intervalo = patch.object(modulo, "aguardar_intervalo_api")
        intervalo.start()
        self.addCleanup(intervalo.stop)

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


class RecuperacaoApiTests(unittest.TestCase):
    def setUp(self):
        for atributo, valor in (
            ("ACCESS_TOKEN", "ficticio"),
            ("SECRET_ACCESS_TOKEN", "ficticio"),
        ):
            p = patch.object(modulo, atributo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(modulo, "aguardar_intervalo_api")
        self.intervalo = p.start()
        self.addCleanup(p.stop)
        p = patch.object(modulo.time, "sleep")
        self.esperar = p.start()
        self.addCleanup(p.stop)

    def resposta(self, status, retry_after=None):
        resposta = requests.Response()
        resposta.status_code = status
        resposta._content = b'{"data": []}'
        resposta._content_consumed = True
        if retry_after is not None:
            resposta.headers["Retry-After"] = retry_after
        return resposta

    def test_429_respeita_retry_after_e_recupera(self):
        with patch.object(modulo.requests, "get", side_effect=[
            self.resposta(429, "2"), self.resposta(200)
        ]) as get:
            self.assertEqual(modulo.consultar_api("vendas"), {"data": []})
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.intervalo.call_count, 2)
        self.esperar.assert_called_once_with(2)

    def test_falha_persistente_para_em_tres_tentativas(self):
        with patch.object(modulo.requests, "get", side_effect=[
            self.resposta(503), self.resposta(503), self.resposta(503)
        ]) as get, self.assertRaises(requests.HTTPError):
            modulo.consultar_api("vendas")
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in self.esperar.call_args_list], [1, 2])

    def test_timeout_temporario_recupera(self):
        with patch.object(modulo.requests, "get", side_effect=[
            requests.Timeout("teste"), self.resposta(200)
        ]) as get:
            self.assertEqual(modulo.consultar_api("clientes"), {"data": []})
        self.assertEqual(get.call_count, 2)

    def test_timeout_persistente_e_propagado(self):
        with patch.object(modulo.requests, "get", side_effect=requests.Timeout("teste")) as get, \
             self.assertRaises(requests.Timeout):
            modulo.consultar_api("clientes")
        self.assertEqual(get.call_count, 3)

    def test_401_nao_repete_credenciais_recusadas(self):
        with patch.object(modulo.requests, "get", return_value=self.resposta(401)) as get, \
             self.assertRaises(requests.HTTPError):
            modulo.consultar_api("clientes")
        get.assert_called_once()
        self.esperar.assert_not_called()

    def test_retry_after_longo_nao_e_encurtado(self):
        with patch.object(modulo.requests, "get", return_value=self.resposta(429, "120")) as get, \
             self.assertRaises(requests.HTTPError):
            modulo.consultar_api("clientes")
        get.assert_called_once()
        self.esperar.assert_not_called()

    def test_retry_after_data_http_e_valores_invalidos(self):
        agora = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        self.assertEqual(modulo.espera_retentativa_api(
            self.resposta(429, "Fri, 09 Oct 2026 12:00:05 GMT"), 0, agora
        ), 5)
        self.assertEqual(modulo.espera_retentativa_api(self.resposta(429, "inválido"), 1), 2)
        for valor in ("NaN", "Infinity", "-1"):
            self.assertIsNone(modulo.espera_retentativa_api(self.resposta(429, valor), 0))


class IntervaloApiTests(unittest.TestCase):
    def test_chamadas_rapidas_aguardam_e_chamadas_lentas_nao(self):
        with patch.object(modulo, "_ultima_requisicao_api", 10), \
             patch.object(modulo.time, "monotonic", side_effect=[10.1, 10.35, 11, 11]), \
             patch.object(modulo.time, "sleep") as esperar:
            modulo.aguardar_intervalo_api()
            modulo.aguardar_intervalo_api()
        esperar.assert_called_once()
        self.assertAlmostEqual(esperar.call_args.args[0], 0.25)


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
        with patch.object(modulo, "listar_todos", side_effect=[clientes, [{"id": "1"}], vendas, []]), \
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
            [{"id": 1}], [{"id": "1"}], [{"id": 1, "cliente_id": 1, "valor_total": 1, "data": "inválida"}], []
        ]), self.assertRaisesRegex(RuntimeError, "data"):
            modulo.gerar_base_clientes()

    def test_paginacao_clientes_e_vendas_integrada(self):
        respostas = [
            {"dados": [{"id": 1, "nome": "A"}], "meta": {"proxima_pagina": 2}},
            {"dados": [{"id": 2, "nome": "B"}], "meta": {"proxima_pagina": None}},
            {"data": [{"id": "1"}], "meta": {"proxima_url": None}},
            {"data": [{"id": 10, "cliente_id": 1, "valor_total": "100", "data": "2026-10-01"}],
             "meta": {"proxima_url": "/api/vendas?pagina=2"}},
            {"data": [{"id": 11, "cliente_id": 1, "valor_total": "300", "data": "2026-10-08"}],
             "meta": {"proxima_url": None}},
            {"data": [], "meta": {"proxima_url": None}},
        ]
        with patch.object(modulo, "consultar_api", side_effect=respostas) as consulta, \
             patch.object(modulo, "hoje_local", return_value=HOJE):
            base = modulo.gerar_base_clientes()
        self.assertEqual(base[0]["compras"], 2)
        self.assertEqual(base[0]["total_gasto"], 400)
        self.assertEqual(base[0]["ticket_medio"], 200)
        self.assertEqual(base[0]["ultima_compra"], "2026-10-08")
        self.assertEqual([c.args[0] for c in consulta.call_args_list],
                         ["clientes", "clientes", "lojas", "vendas", "vendas", "vendas"])

    def test_aniversario_29_fevereiro(self):
        with patch.object(modulo, "hoje_local", return_value=date(2027, 2, 27)):
            self.assertEqual(modulo.dias_ate_aniversario("2000-02-29"), 1)


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

    def test_rotas_atuais_preservadas(self):
        rotas = self.client.get("/rotas").json
        for rota in ("/", "/health", "/teste-clientes-vendas", "/teste-gestaoclick",
                     "/teste-vendas", "/debug-clientes", "/debug-vendas"):
            self.assertIn(rota, rotas)



class VendasBalcaoTests(unittest.TestCase):
    def test_inclui_balcao_sem_duplicar_venda_da_consulta_padrao(self):
        venda = {"id": 1}
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": "1"}], [venda], [{"id": "1"}, {"id": 2}]
        ]) as listar:
            resultado = modulo.listar_vendas()
        self.assertEqual(resultado, [venda, {"id": 2}])
        self.assertEqual(listar.call_args_list[2].args, (
            "vendas", {"loja_id": "1", "tipo": "vendas_balcao"}
        ))

    def test_falha_balcao_nao_devolve_historico_parcial(self):
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": "1"}], [{"id": 1}], requests.Timeout("teste")
        ]), self.assertRaises(requests.Timeout):
            modulo.listar_vendas()

    def test_venda_sem_id_nao_pode_ser_contada_duas_vezes(self):
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": "1"}], [{"cliente_id": 1}], []
        ]), self.assertRaisesRegex(RuntimeError, "sem ID"):
            modulo.listar_vendas()


    def test_matriz_filial_e_balcao_sem_duplicar(self):
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": 1}, {"id": 2}],
            [{"id": 10}], [{"id": 11}],
            [{"id": "10"}, {"id": 12}], [{"id": 13}],
        ]) as listar:
            vendas = modulo.listar_vendas()
        self.assertEqual([v["id"] for v in vendas], [10, 11, 12, 13])
        self.assertEqual([c.args for c in listar.call_args_list], [
            ("lojas",),
            ("vendas", {"loja_id": "1"}),
            ("vendas", {"loja_id": "1", "tipo": "vendas_balcao"}),
            ("vendas", {"loja_id": "2"}),
            ("vendas", {"loja_id": "2", "tipo": "vendas_balcao"}),
        ])

    def test_falha_filial_nao_devolve_so_matriz(self):
        with patch.object(modulo, "listar_todos", side_effect=[
            [{"id": 1}, {"id": 2}], [{"id": 10}], [], requests.Timeout("teste")
        ]), self.assertRaises(requests.Timeout):
            modulo.listar_vendas()

    def test_lojas_ausentes_ou_sem_id_falham(self):
        for lojas in ([], [{"nome": "Matriz"}]):
            with self.subTest(lojas=lojas), patch.object(
                modulo, "listar_todos", return_value=lojas
            ), self.assertRaisesRegex(RuntimeError, "loja|Loja"):
                modulo.listar_vendas()

    def test_paginacao_balcao_preserva_loja_e_tipo(self):
        respostas = [
            {"data": [{"id": 1}], "meta": {"proxima_url": None}},
            {"data": [], "meta": {"proxima_url": None}},
            {"data": [{"id": 10}], "meta": {"proxima_url": "/api/vendas?pagina=2"}},
            {"data": [{"id": 11}], "meta": {"proxima_url": None, "total_registros": 2}},
        ]
        with patch.object(modulo, "consultar_api", side_effect=respostas) as consultar:
            vendas = modulo.listar_vendas()
        self.assertEqual(len(vendas), 2)
        self.assertEqual(consultar.call_args_list[-1].args, (
            "vendas", {"loja_id": "1", "tipo": "vendas_balcao", "pagina": 2, "limite": 100}
        ))


class DiagnosticoBalcaoTests(unittest.TestCase):
    def test_estrutura_remove_valores_pessoais_em_todos_os_niveis(self):
        dados = {"nome_cliente": "Pessoa Ficticia", "cpf": "12345678900",
                 "consumidor": {"telefone": "38912345678"},
                 "atributos": [{"valor": "email@teste.invalid"}], "data": None}
        saida = str(diagnostico.estrutura(dados))
        for valor in ("Pessoa Ficticia", "12345678900", "38912345678", "email@teste.invalid"):
            self.assertNotIn(valor, saida)
        self.assertIn("telefone", saida)
        self.assertEqual(diagnostico.estrutura(dados)["data"], "null")

    def test_filtra_codigo_consulta_filial_e_nao_duplica(self):
        venda = {"id": 10, "codigo": 1759, "cliente_id": "", "nome_cliente": "Ficticio"}
        with patch.object(diagnostico, "listar_todos", side_effect=[
            [{"id": 1}, {"id": 2}], [venda, {"id": 11, "codigo": 1760}],
            [venda, {"id": 12, "codigo": "1759", "cliente_id": 1}],
        ]) as listar:
            resultado = diagnostico.diagnosticar(1759)
        self.assertEqual(resultado["vendas_encontradas"], 2)
        self.assertFalse(resultado["vendas"][0]["cliente_id_preenchido"])
        self.assertTrue(resultado["vendas"][1]["cliente_id_preenchido"])
        self.assertEqual(listar.call_args_list[-1].args, (
            "vendas", {"loja_id": "2", "tipo": "vendas_balcao", "codigo": 1759}
        ))

    def test_falha_filial_interrompe_diagnostico(self):
        with patch.object(diagnostico, "listar_todos", side_effect=[
            [{"id": 1}, {"id": 2}], [], requests.Timeout("teste")
        ]), self.assertRaises(requests.Timeout):
            diagnostico.diagnosticar(1759)


if __name__ == "__main__":
    unittest.main()



import base64
import re
import unittest
from unittest.mock import patch
from uuid import uuid4
from datetime import datetime
import app as modulo
import agenda


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.cfg = patch.dict(modulo.app.config, SMARTONE_ADMIN_USER='admin', SMARTONE_ADMIN_PASSWORD='senha-ficticia-longa-para-teste')
        self.cfg.start()
        self.addCleanup(self.cfg.stop)
        self.c = modulo.app.test_client()
        self.auth = {'Authorization': 'Basic ' + base64.b64encode(b'admin:senha-ficticia-longa-para-teste').decode()}
        self.dados = dict(nome='Cliente Teste', telefone='(38) 99999-9999', aparelho='Samsung A54', servico='Troca de tela', responsavel='Dalvan', os='123', inicio='2026-10-15T09:00', entrega='2026-10-15T15:00', status='Agendado', observacoes='<script>alert(1)</script>', cliente_id='1')

    def token(self):
        r = self.c.get('/agenda/novo', headers=self.auth)
        self.assertEqual(r.status_code, 200)
        return re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)

    def test_rotas_exigem_autenticacao(self):
        with patch.object(agenda.store, 'consultar') as db:
            for path in ['/agenda', '/agenda/novo', '/agenda/clientes?q=ana']:
                self.assertEqual(self.c.get(path).status_code, 401)
            self.assertEqual(self.c.post('/agenda/salvar').status_code, 401)
            db.assert_not_called()

    def test_sem_csrf_nao_grava(self):
        with patch.object(agenda.store, 'salvar') as gravar:
            self.assertEqual(self.c.post('/agenda/salvar', data=self.dados, headers=self.auth).status_code, 403)
            gravar.assert_not_called()

    def test_cria_e_redireciona_com_telefone_normalizado(self):
        with patch.object(agenda.store, 'salvar') as gravar:
            r = self.c.post('/agenda/salvar', data=dict(self.dados, csrf=self.token()), headers=self.auth)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(gravar.call_args.args[1]['telefone'], '38999999999')
            self.assertEqual(gravar.call_args.args[2], 0)

    def test_validacao_preserva_campos_sem_gravar(self):
        for alteracao in [dict(entrega='2026-10-14T12:00'), dict(telefone='123'), dict(status='Inventado'), dict(nome='')]:
            with patch.object(agenda.store, 'salvar') as gravar:
                r = self.c.post('/agenda/salvar', data=dict(self.dados, csrf=self.token(), **alteracao), headers=self.auth)
                self.assertEqual(r.status_code, 400)
                self.assertIn('Samsung A54', r.text)
                self.assertNotIn('<script>alert(1)</script>', r.text)
                gravar.assert_not_called()

    def test_banco_indisponivel_preserva_formulario(self):
        with patch.object(agenda.store, 'salvar', side_effect=RuntimeError('offline')):
            r = self.c.post('/agenda/salvar', data=dict(self.dados, csrf=self.token()), headers=self.auth)
            self.assertEqual(r.status_code, 503)
            self.assertIn('Samsung A54', r.text)

    def test_semana_e_dados_escapados(self):
        row = dict(self.dados, id=str(uuid4()), versao=1, nome='<img src=x onerror=alert(1)>')
        with patch.object(agenda.store, 'consultar', return_value=([row], 1, 1)) as consultar:
            r = self.c.get('/agenda?data=2026-10-15&visao=semana', headers=self.auth)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(consultar.call_args.args[:2], (datetime(2026, 10, 12), datetime(2026, 10, 19)))
            self.assertNotIn('<img src=x', r.text)
            self.assertIn('wa.me/55', r.text)

    def test_edicao_usa_versao_assinada_e_conflito(self):
        identificador = str(uuid4())
        row = dict(self.dados, id=identificador, versao=4)
        with patch.object(agenda.store, 'obter', return_value=row):
            r = self.c.get('/agenda/' + identificador + '/editar', headers=self.auth)
        csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
        with patch.object(agenda.store, 'salvar', side_effect=ValueError('Este serviço foi alterado em outra tela.')) as salvar:
            r = self.c.post('/agenda/salvar', data=dict(self.dados, csrf=csrf, versao=9), headers=self.auth)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(salvar.call_args.args[2], 4)

    def test_indisponibilidade_nao_parece_agenda_vazia(self):
        with patch.object(agenda.store, 'consultar', side_effect=RuntimeError('offline')):
            r = self.c.get('/agenda', headers=self.auth)
            self.assertEqual(r.status_code, 503)
            self.assertNotIn('Nenhum serviço encontrado', r.text)


if __name__ == '__main__':
    unittest.main()

import unittest
from concurrent.futures import Future
from unittest.mock import Mock
from base_cache import BaseCache, BaseCarregando


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.futuro = Future()
        self.executor = Mock()
        self.executor.submit.return_value = self.futuro
        self.tempo = 0
        self.cache = BaseCache(Mock(), executor=self.executor, relogio=lambda: self.tempo)

    def iniciar(self):
        with self.assertRaises(BaseCarregando):
            self.cache.obter()

    def test_primeiros_acessos_compartilham_uma_importacao(self):
        for _ in range(5):
            self.iniciar()
        self.executor.submit.assert_called_once()
        self.futuro.set_result([{"id": "1"}])
        self.assertEqual(self.cache.obter(), [{"id": "1"}])
        self.assertEqual(self.cache.obter(), [{"id": "1"}])
        self.executor.submit.assert_called_once()

    def test_base_vazia_completa_e_valida(self):
        self.iniciar()
        self.futuro.set_result([])
        self.assertEqual(self.cache.obter(), [])

    def test_atualizacao_falha_preserva_base_anterior(self):
        self.iniciar()
        self.futuro.set_result([1])
        self.cache.obter()
        self.tempo = 301
        atualizacao = Future()
        self.executor.submit.return_value = atualizacao
        self.assertEqual(self.cache.obter(), [1])
        self.assertEqual(self.cache.estado(), "atualizando")
        atualizacao.set_exception(RuntimeError("erro"))
        self.assertEqual(self.cache.obter(), [1])
        self.assertEqual(self.cache.estado(), "erro")
        self.assertEqual(self.executor.submit.call_count, 2)

    def test_falha_inicial_nao_publica_base_zerada_e_permite_retry(self):
        self.iniciar()
        self.futuro.set_exception(RuntimeError("erro"))
        with self.assertRaises(RuntimeError):
            self.cache.obter()
        self.tempo = 31
        self.executor.submit.return_value = Future()
        self.iniciar()
        self.assertEqual(self.executor.submit.call_count, 2)

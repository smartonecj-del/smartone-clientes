"""Uma importação por processo; nunca publica uma base parcial."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic


class BaseCarregando(Exception):
    pass


class BaseCache:
    def __init__(self, carregar, ttl=300, intervalo_erro=30, executor=None, relogio=monotonic):
        self.carregar = carregar
        self.ttl = ttl
        self.intervalo_erro = intervalo_erro
        self.executor = executor or ThreadPoolExecutor(max_workers=1)
        self.relogio = relogio
        self.lock = Lock()
        self.futuro = None
        self.dados = None
        self.proxima = 0
        self.erro = False

    def obter(self):
        with self.lock:
            agora = self.relogio()
            if self.futuro is not None and self.futuro.done():
                try:
                    dados = self.futuro.result()
                except Exception:
                    self.erro = True
                    self.proxima = agora + self.intervalo_erro
                else:
                    self.dados = dados
                    self.erro = False
                    self.proxima = agora + self.ttl
                self.futuro = None
            if self.futuro is None and agora >= self.proxima:
                self.futuro = self.executor.submit(self.carregar)
            if self.dados is not None:
                return self.dados
            if self.erro and self.futuro is None:
                raise RuntimeError("Falha na sincronização. Nova tentativa em alguns segundos.")
            raise BaseCarregando()

    def estado(self):
        with self.lock:
            if self.futuro is not None:
                return "atualizando"
            return "erro" if self.erro else "concluido"

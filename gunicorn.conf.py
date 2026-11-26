# Mantém a verificação de saúde disponível durante consultas ao ERP.
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120


def post_worker_init(worker):
    from parceiros import conexao, preparar
    import psycopg

    try:
        with conexao() as conn:
            preparar(conn)
        worker.log.info("Cadastro de parceiros disponível no PostgreSQL.")
    except (RuntimeError, psycopg.Error):
        # A próxima consulta repete a inicialização se o banco voltar.
        worker.log.warning("Cadastro de parceiros aguardando conexão com PostgreSQL.")

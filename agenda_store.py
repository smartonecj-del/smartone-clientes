"""Agenda persistente no PostgreSQL, separada dos aniversários e do ERP."""
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from aniversarios_store import conexao


def preparar(conn):
    # Serializa apenas a criação inicial, evitando corrida entre workers.
    conn.execute('SELECT pg_advisory_xact_lock(17340521)')
    conn.execute('''CREATE TABLE IF NOT EXISTS smartone_agenda (
        id UUID PRIMARY KEY, dados JSONB NOT NULL,
        inicio TIMESTAMP NOT NULL, entrega TIMESTAMP NOT NULL,
        status TEXT NOT NULL, versao INTEGER NOT NULL DEFAULT 1,
        atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
    conn.execute('CREATE INDEX IF NOT EXISTS smartone_agenda_inicio ON smartone_agenda (inicio)')
    conn.execute('CREATE INDEX IF NOT EXISTS smartone_agenda_entrega ON smartone_agenda (entrega)')


def apresentar(row):
    return dict(row['dados'], id=str(row['id']), versao=row['versao']) if row else None


def consultar(inicio, fim, agora, atrasados=False):
    with conexao() as conn:
        preparar(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            filtro = "status NOT IN ('Entregue', 'Cancelado') AND entrega < %s" if atrasados else '((inicio >= %s AND inicio < %s) OR (entrega >= %s AND entrega < %s))'
            args = (agora,) if atrasados else (inicio, fim, inicio, fim)
            cur.execute('SELECT count(*) AS total FROM smartone_agenda WHERE ' + filtro, args)
            total = cur.fetchone()['total']
            cur.execute('SELECT * FROM smartone_agenda WHERE ' + filtro + ' ORDER BY inicio, id LIMIT 500', args)
            rows = [apresentar(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*) AS total FROM smartone_agenda WHERE status NOT IN ('Entregue', 'Cancelado') AND entrega < %s", (agora,))
            return rows, total, cur.fetchone()['total']


def obter(identificador):
    with conexao() as conn:
        preparar(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM smartone_agenda WHERE id = %s', (identificador,))
            return apresentar(cur.fetchone())


def salvar(identificador, dados, versao):
    with conexao() as conn:
        preparar(conn)
        valores = (Jsonb(dados), dados['inicio'], dados['entrega'], dados['status'])
        if versao == 0:
            # O UUID assinado do formulário impede duplicação por reenvio.
            conn.execute('''INSERT INTO smartone_agenda (dados, inicio, entrega, status, id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING''', (*valores, identificador))
        else:
            result = conn.execute('''UPDATE smartone_agenda SET dados=%s, inicio=%s,
                entrega=%s, status=%s, versao=versao+1, atualizado_em=NOW()
                WHERE id=%s AND versao=%s''', (*valores, identificador, versao))
            if result.rowcount != 1:
                raise ValueError('Este serviço foi alterado em outra tela. Reabra a edição antes de salvar.')

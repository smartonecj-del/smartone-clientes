"""Cadastro interno de parceiros, indicações e vendas atribuídas pela equipe."""
import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from flask import abort, redirect, render_template, request, url_for
from psycopg.rows import dict_row

from aniversarios_store import conexao


def preparar(conn):
    conn.execute("SELECT pg_advisory_xact_lock(17340522)")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_parceiros (
        id BIGSERIAL PRIMARY KEY, nome TEXT NOT NULL, contato TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_indicacoes (
        id UUID PRIMARY KEY, parceiro BIGINT NOT NULL REFERENCES smartone_parceiros,
        cliente TEXT NOT NULL, contato TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_vendas_indicadas (
        referencia TEXT PRIMARY KEY, indicacao UUID NOT NULL REFERENCES smartone_indicacoes,
        data DATE NOT NULL, valor NUMERIC(14,2) NOT NULL CHECK (valor > 0))""")


def campo(nome, limite, obrigatorio=True):
    valor = request.form.get(nome, '').strip()
    if (obrigatorio and not valor) or len(valor) > limite:
        raise ValueError('Confira o preenchimento do campo ' + nome + '.')
    return valor


def gravar(conn, hoje):
    acao = campo('acao', 20)
    if acao == 'parceiro':
        nome = campo('nome', 120)
        contato = campo('contato', 80, False)
        existente = conn.execute(
            "SELECT id FROM smartone_parceiros WHERE lower(nome)=lower(%s) AND contato=%s",
            (nome, contato)).fetchone()
        if existente:
            return existente[0]
        return conn.execute("INSERT INTO smartone_parceiros(nome, contato) VALUES (%s,%s) RETURNING id",
                            (nome, contato)).fetchone()[0]
    if acao == 'indicacao':
        parceiro = int(campo('parceiro', 18))
        conn.execute("""INSERT INTO smartone_indicacoes(id, parceiro, cliente, contato)
                     VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                     (UUID(campo('id', 36)), parceiro, campo('cliente', 120), campo('contato', 80, False)))
        return parceiro
    if acao != 'venda':
        abort(400)
    indicacao = UUID(campo('indicacao', 36))
    referencia = campo('referencia', 80).upper()
    valor = campo('valor', 16).replace(',', '.')
    if not re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?', valor) or Decimal(valor) <= 0:
        raise ValueError('Informe um valor de venda maior que zero, com até duas casas decimais.')
    dia = date.fromisoformat(campo('data', 10))
    if dia > hoje:
        raise ValueError('A data da venda deve ser hoje ou anterior.')
    conn.execute("""INSERT INTO smartone_vendas_indicadas(referencia, indicacao, data, valor)
                 VALUES (%s,%s,%s,%s)""", (referencia, indicacao, dia, Decimal(valor)))
    return conn.execute('SELECT parceiro FROM smartone_indicacoes WHERE id=%s', (indicacao,)).fetchone()[0]


def registrar_parceiros(app, token, validar, moeda):

    @app.route('/parceiros', methods=['GET', 'POST'])
    def parceiros():
        hoje = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        selecionado = request.args.get('parceiro', type=int)
        resposta = dict(parceiros=[], indicacoes=[], selecionado=None, vendas={}, hoje=hoje,
                        csrf=token(), moeda=moeda, novo_id=uuid4(), erro=None)
        status = 200
        if request.method == 'POST' and not validar(request.form.get('csrf')):
            abort(403)
        try:
            with conexao() as conn:
                preparar(conn)
                if request.method == 'POST':
                    try:
                        with conn.transaction():
                            selecionado = gravar(conn, hoje)
                        return redirect(url_for('parceiros', parceiro=selecionado), code=303)
                    except psycopg.errors.UniqueViolation:
                        resposta['erro'] = 'Já existe uma venda com esse número.'
                    except (ValueError, psycopg.errors.ForeignKeyViolation) as exc:
                        resposta['erro'] = str(exc) if type(exc) is ValueError and str(exc).startswith(('Confira', 'Informe', 'A data')) else 'Confira os dados informados.'
                    status = 400
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("""SELECT p.id,p.nome,p.contato, count(DISTINCT i.id) AS indicacoes,
                                count(v.referencia) AS vendas, coalesce(sum(v.valor),0) AS total
                                FROM smartone_parceiros p LEFT JOIN smartone_indicacoes i ON i.parceiro=p.id
                                LEFT JOIN smartone_vendas_indicadas v ON v.indicacao=i.id
                                GROUP BY p.id ORDER BY total DESC,p.nome,p.id""")
                    resposta['parceiros'] = cur.fetchall()
                    resposta['selecionado'] = next((p for p in resposta['parceiros'] if p['id']==selecionado), None)
                    if resposta['selecionado']:
                        cur.execute('SELECT * FROM smartone_indicacoes WHERE parceiro=%s ORDER BY cliente,id', (selecionado,))
                        resposta['indicacoes'] = cur.fetchall()
                        cur.execute("""SELECT v.* FROM smartone_vendas_indicadas v JOIN smartone_indicacoes i
                                    ON i.id=v.indicacao WHERE i.parceiro=%s ORDER BY v.data DESC,v.referencia""", (selecionado,))
                        for venda in cur:
                            resposta['vendas'].setdefault(venda['indicacao'], []).append(venda)
        except (psycopg.Error, RuntimeError):
            app.logger.exception('Banco de parceiros indisponível')
            resposta['erro'] = 'Não foi possível acessar os cadastros. Tente novamente em instantes.'
            status = 503
        return render_template('parceiros.html', **resposta), status

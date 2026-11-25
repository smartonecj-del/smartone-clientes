"""Extrato individual para compartilhamento pelo lojista."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def gerar_pdf(nome, inicio, vendas, moeda):
    arquivo = BytesIO()
    doc = SimpleDocTemplate(arquivo, pagesize=A4, leftMargin=32, rightMargin=32,
                            title='Smart One | Relatório de comissão')
    estilos = getSampleStyleSheet()
    periodo = inicio.strftime('%m/%Y') if inicio else 'Todo o período'
    elementos = [Paragraph('Smart One | Relatório de comissão', estilos['Title']),
                 Paragraph(escape(nome), estilos['Heading2']),
                 Paragraph(periodo, estilos['Normal']), Spacer(1, 12)]
    total = sum(v['valor'] for v in vendas)
    classificadas = [v for v in vendas if v['comissao'] is not None]
    comissao = sum(v['comissao'] for v in classificadas)
    elementos += [Paragraph(f'{len(vendas)} venda(s) · Total vendido: {moeda(total)}', estilos['Normal']),
                  Paragraph('Comissão apurada: ' + moeda(comissao), estilos['Heading2']),
                  Paragraph('5% sobre serviços e 3% sobre produtos.', estilos['Normal'])]
    pendentes = len(vendas) - len(classificadas)
    if pendentes:
        elementos.append(Paragraph(f'{pendentes} venda(s) aguardam classificação para apurar a comissão.', estilos['Normal']))
    if vendas:
        linhas = [['Data', 'Venda', 'Total vendido', 'Serviços', 'Produtos', 'Comissão']]
        for v in vendas:
            servicos = v['servicos']
            linhas.append([v['data'].strftime('%d/%m/%Y'), escape(v['referencia']), moeda(v['valor']),
                           moeda(servicos) if servicos is not None else '—',
                           moeda(v['valor'] - servicos) if servicos is not None else '—',
                           moeda(v['comissao']) if servicos is not None else 'Pendente'])
        tabela = Table([[Paragraph(c, estilos['BodyText']) for c in linha] for linha in linhas],
                       colWidths=[72, 88, 94, 92, 92, 92], repeatRows=1)
        tabela.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8f1fc')),
                                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                    ('LINEBELOW', (0, 0), (-1, -1), 0.3, colors.lightgrey)]))
        elementos += [Spacer(1, 14), tabela]
    doc.build(elementos)
    arquivo.seek(0)
    return arquivo

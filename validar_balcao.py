"""Diagnóstico privado e somente leitura; usa as variáveis existentes do app.

Executar no ambiente de revisão autenticado: python validar_balcao.py --codigo 1759
Não cria rota pública e não imprime tokens ou valores de campos pessoais.
"""
import argparse
import json

from app import listar_todos


def estrutura(valor):
    """Preserva nomes de campos e tipos, removendo todos os valores."""
    if isinstance(valor, dict):
        return {campo: estrutura(conteudo) for campo, conteudo in valor.items()}
    if isinstance(valor, list):
        tipos = {}
        for item in valor:
            tipo = estrutura(item)
            tipos[json.dumps(tipo, sort_keys=True)] = tipo
        return {"tipo": "lista", "itens": list(tipos.values())}
    if valor is None:
        return "null"
    if isinstance(valor, bool):
        return "booleano"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, (int, float)):
        return "numero"
    return type(valor).__name__


def diagnosticar(codigo):
    lojas = listar_todos("lojas")
    if not lojas:
        raise RuntimeError("Nenhuma loja acessível para validar a venda.")
    resultado = []
    ids_vistos = set()
    for loja in lojas:
        if loja.get("id") in (None, ""):
            raise RuntimeError("Loja sem ID no GestãoClick.")
        vendas = listar_todos("vendas", {
            "loja_id": str(loja["id"]), "tipo": "vendas_balcao", "codigo": codigo,
        })
        for venda in vendas:
            # Confirma o código mesmo se o servidor ignorar o filtro.
            if str(venda.get("codigo")) != str(codigo):
                continue
            venda_id = venda.get("id")
            if venda_id in (None, ""):
                raise RuntimeError("Venda sem ID no GestãoClick.")
            venda_id = str(venda_id)
            if venda_id in ids_vistos:
                continue
            ids_vistos.add(venda_id)
            resultado.append({
                "cliente_id_preenchido": venda.get("cliente_id") not in (None, "", 0, "0"),
                "estrutura": estrutura(venda),
            })
    return {"lojas_consultadas": len(lojas), "vendas_encontradas": len(resultado),
            "vendas": resultado}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codigo", type=int, default=1759)
    args = parser.parse_args()
    try:
        print(json.dumps(diagnosticar(args.codigo), ensure_ascii=False, indent=2))
    except Exception:
        # Exceções HTTP podem incluir URLs ou conteúdo; não imprimir o traceback.
        parser.exit(1, "Diagnóstico não concluído. Verifique credenciais, permissões e conexão no ambiente privado.\n")

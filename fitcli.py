"""fitcli - CLI para explorar a API wger (Workout Manager).

TP1 de ALPCD 2026/27, com os módulos requests, re e typer.

Uso:
    python fitcli.py top 10
    python fitcli.py search biceps 5
    python fitcli.py type 9
    python fitcli.py equipment chest
    python fitcli.py workout biceps,back compound 5 --csv treino.csv
    python fitcli.py grupos              (extra: músculos e categorias válidos)
"""

import csv
import html
import json
import re
import sys

import requests
import typer

# ---------------------------------------------------------------------------
# 1. Ligação à API
# ---------------------------------------------------------------------------

BASE = "https://wger.de/api/v2/"
CABECALHOS = {"User-Agent": "fitcli/1.0 (TP1 ALPCD - Universidade do Minho)"}
INGLES = 2  # id da língua inglesa na wger

app = typer.Typer(help="Pesquisa exercícios físicos na API wger.")


def pedido(url, params=None):
    """Faz um GET à API e devolve o JSON. Se o status não for 200, mostra o erro e sai."""
    if not url.startswith("http"):
        url = BASE + url
    try:
        r = requests.get(url, params=params, headers=CABECALHOS, timeout=20)
    except requests.RequestException as erro:
        typer.echo(f"Erro de ligação à API: {erro}", err=True)
        raise typer.Exit(1)
    if r.status_code == 404:
        typer.echo("Não encontrado (404).", err=True)
        raise typer.Exit(1)
    if r.status_code != 200:
        typer.echo(f"A API respondeu com o código {r.status_code}.", err=True)
        raise typer.Exit(1)
    return r.json()


# ---------------------------------------------------------------------------
# 2. Tratar os dados de um exercício
# ---------------------------------------------------------------------------

def traducao_inglesa(ex):
    """Devolve a tradução em inglês de um exercício (ou None se não houver)."""
    for t in ex["translations"]:
        if t["language"] == INGLES:
            return t
    return None


def limpar_html(texto):
    """Tira as etiquetas HTML da descrição com expressões regulares."""
    if texto is None:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)   # <p>, <li>, </ol> ... passam a espaço
    texto = html.unescape(texto)              # &amp; passa a &
    texto = re.sub(r"\s+", " ", texto)        # vários espaços seguidos passam a um só
    return texto.strip()


def musculos(ex):
    """Nomes dos músculos (principais e secundários), com o nome comum se existir."""
    nomes = []
    for m in ex["muscles"] + ex["muscles_secondary"]:
        if m["name_en"]:
            nomes.append(m["name_en"])
        else:
            nomes.append(m["name"])
    return nomes


def resumo(ex):
    """O dicionário que se mostra ao utilizador."""
    equipamento = []
    for e in ex["equipment"]:
        equipamento.append(e["name"])
    return {
        "id": ex["id"],
        "name": traducao_inglesa(ex)["name"],
        "category": ex["category"]["name"],
        "equipment": equipamento,
        "muscles": musculos(ex),
    }


def exercicios(filtros, maximo=None):
    """Lista de exercícios em inglês que cumprem os filtros (no máximo 'maximo').

    Vai buscando páginas pelo campo 'next' enquanto houver mais e ainda
    faltarem exercícios. Sem 'maximo', percorre as páginas todas.
    """
    params = {"language": INGLES, "limit": 100}
    if maximo is not None and maximo < 100:
        params["limit"] = maximo  # para o top 3 não descarregar 100 exercícios
    params.update(filtros)

    resultado = []
    url = "exerciseinfo/"
    while url:
        dados = pedido(url, params)
        for ex in dados["results"]:
            if traducao_inglesa(ex) is None:  # há exercícios só noutras línguas
                continue
            resultado.append(ex)
            if maximo is not None and len(resultado) == maximo:
                return resultado
        url = dados["next"]  # None na última página
        params = None        # o link 'next' já traz os parâmetros todos
    return resultado


# ---------------------------------------------------------------------------
# 3. Músculos e categorias: converter o nome escrito num filtro da API
# ---------------------------------------------------------------------------

def normalizar(s):
    """Minúsculas e só letras: "Biceps brachii" fica "bicepsbrachii"."""
    if s is None:
        return ""
    return re.sub(r"[^a-z]", "", s.lower())


def filtro_de(nome):
    """Converte 'biceps' ou 'arms' no filtro da API ({"muscles": 1} ou {"category": 8})."""
    alvo = normalizar(nome)
    erro = f"'{nome}' não é um músculo nem uma categoria. Veja: python fitcli.py grupos"
    if alvo == "":  # "123" fica vazio, e "" corresponderia a qualquer músculo
        raise typer.BadParameter(erro)

    for c in pedido("exercisecategory/")["results"]:
        if normalizar(c["name"]) == alvo:
            return {"category": c["id"]}

    lista_musculos = pedido("muscle/", {"limit": 100})["results"]
    for m in lista_musculos:
        if alvo == normalizar(m["name"]) or alvo == normalizar(m["name_en"]):
            return {"muscles": m["id"]}
    # sem nome igual: aceita o início do nome ("pector" dá Pectoralis major)
    for m in lista_musculos:
        if normalizar(m["name"]).startswith(alvo) or normalizar(m["name_en"]).startswith(alvo):
            return {"muscles": m["id"]}

    raise typer.BadParameter(erro)


# ---------------------------------------------------------------------------
# 4. Tipo de esforço (alínea c)
# ---------------------------------------------------------------------------

# Palavras que indicam o tipo de exercício, procuradas no nome e na descrição.
# \b = fronteira de palavra; s? = plural opcional; (?:...) = grupo sem captura.
CARDIO = re.compile(r"\b(?:run(?:ning)?|jog(?:ging)?|sprint|cycl(?:e|ing)|bike|row(?:ing)? machine|"
                    r"jump(?:ing)? jacks?|burpees?|skipping|jump rope|treadmill|elliptical|swim(?:ming)?)\b", re.I)
COMPOSTO = re.compile(r"\b(?:squats?|deadlifts?|bench press|press(?:es)?|rows?|pull[- ]?ups?|chin[- ]?ups?|"
                      r"push[- ]?ups?|dips?|lunges?|cleans?|snatch|thrusters?|step[- ]?ups?|hip thrusts?|swings?|"
                      r"pull[- ]?downs?|high pulls?|ohp|good mornings?|roll[- ]?outs?)\b", re.I)
ISOLADO = re.compile(r"\b(?:curls?|raises?|fl(?:y|ies|yes)|extensions?|kickbacks?|shrugs?|crunch(?:es)?|"
                     r"pull[- ]?overs?|pushdowns?|calf|wrist|butterfly|pec deck|triceps? press|skull ?crushers?)\b", re.I)
# Alongamentos, yoga e posições paradas não são treino de força: ficam em "other".
OUTRO = re.compile(r"\b(?:stretch(?:es|ing)?|yoga|mobility|holds?|circles?)\b", re.I)


def tipo_esforco(ex):
    """compound / isolated / cardio / other, por esta ordem de regras:

    1. categoria Cardio, ou palavra de cardio no nome -> cardio
    2. alongamento, yoga ou posição parada no nome -> other
    3. palavra de exercício isolado no NOME -> isolated  (ex.: "Leg Curl", "Calf Press")
    4. palavra de exercício composto no NOME -> compound (ex.: "Barbell Row")
    5. 3 ou mais músculos -> compound (mexe várias articulações)
    6. 1 só músculo -> isolated
    7. as mesmas palavras, mas na descrição
    8. nada disto -> other
    O nome e o número de músculos vêm antes da descrição porque são mais
    fiáveis: "press" ou "row" aparecem em frases normais das descrições.
    Por isso a descrição só decide quando o exercício tem 0 ou 2 músculos.
    """
    t = traducao_inglesa(ex)
    nome = t["name"]
    descricao = limpar_html(t["description"])
    n_musculos = len(ex["muscles"]) + len(ex["muscles_secondary"])

    if ex["category"]["name"] == "Cardio" or CARDIO.search(nome):
        return "cardio"
    if OUTRO.search(nome):
        return "other"
    if ISOLADO.search(nome):
        return "isolated"
    if COMPOSTO.search(nome):
        return "compound"
    if n_musculos >= 3:
        return "compound"
    if n_musculos == 1:
        return "isolated"
    if CARDIO.search(descricao):
        return "cardio"
    if COMPOSTO.search(descricao):
        return "compound"
    if ISOLADO.search(descricao):
        return "isolated"
    return "other"


# ---------------------------------------------------------------------------
# 5. Equipamento nas descrições (alínea d)
# ---------------------------------------------------------------------------

EQUIPAMENTO = {
    "barbell": r"\bbarbells?\b",
    "dumbbell": r"\bdumbbells?\b",
    "cable": r"\bcables?\b",
    "machine": r"\bmachines?\b",
    "bodyweight": r"\bbody[- ]?weight\b",
    "kettlebell": r"\bkettlebells?\b",
    "bench": r"\bbench(?:es)?\b",
    "band": r"\b(?:resistance )?bands?\b",
    "ball": r"\b(?:swiss|exercise|stability|medicine) balls?\b",  # não conta "ball of the foot"
    "pull-up bar": r"\bpull[- ]?up bars?\b",
    "bar": r"\b(?:ez[- ]?)?bars?\b",  # "bar" e "EZ bar"
    "plate": r"\bplates?\b",
    "rope": r"\bropes?\b",
    "roller": r"\brollers?\b",
    "mat": r"\bmats?\b",
}


def contar_equipamento(textos):
    """Conta as menções a cada equipamento e ordena da mais frequente para a menos."""
    contagem = {}
    for nome in EQUIPAMENTO:
        contagem[nome] = 0
    for texto in textos:
        for nome, padrao in EQUIPAMENTO.items():
            contagem[nome] += len(re.findall(padrao, texto, re.I))
    # cada "pull-up bar" também foi contado em "bar", por isso tira-se outra vez
    contagem["bar"] -= contagem["pull-up bar"]

    ordenado = sorted(contagem.items(), key=lambda par: par[1], reverse=True)
    resultado = []
    for nome, n in ordenado:
        if n > 0:
            resultado.append({nome: n})
    return resultado


# ---------------------------------------------------------------------------
# 6. Comandos da CLI
# ---------------------------------------------------------------------------

def escrever_json(dados):
    typer.echo(json.dumps(dados, ensure_ascii=False, indent=2))


@app.command()
def top(n: int = typer.Argument(10, min=1, help="Quantos exercícios mostrar")):
    """a) Os N exercícios mais recentes da base de dados."""
    resultado = []
    for ex in exercicios({"ordering": "-created"}, n):
        resultado.append(resumo(ex))
    escrever_json(resultado)


@app.command()
def search(
    termo: str = typer.Argument(..., help="Músculo (biceps, chest...) ou categoria (arms, legs...)"),
    n: int = typer.Argument(10, min=1, help="Quantos resultados mostrar"),
    categoria: str = typer.Option(None, "--categoria", "-c", help="Juntar também uma categoria (ex.: biceps -c arms)"),
):
    """b) Pesquisa exercícios por músculo e/ou categoria."""
    filtros = filtro_de(termo)
    if categoria:
        filtros.update(filtro_de(categoria))
    resultado = []
    for ex in exercicios(filtros, n):
        resultado.append(resumo(ex))
    if len(resultado) == 0:  # a mensagem vai para o stderr, para não estragar o JSON
        typer.echo(f"Sem resultados para '{termo}'.", err=True)
    escrever_json(resultado)


@app.command("type")
def tipo(exercise_id: int = typer.Argument(..., help="O id do exercício (ver no 'top' ou no 'search')")):
    """c) Tipo de esforço de um exercício: compound, isolated, cardio ou other."""
    ex = pedido(f"exerciseinfo/{exercise_id}/")
    if traducao_inglesa(ex) is None:
        typer.echo("Este exercício não tem versão em inglês.", err=True)
        raise typer.Exit(1)
    typer.echo(tipo_esforco(ex))


@app.command()
def equipment(categoria: str = typer.Argument(..., help="Categoria (abs, arms, back, calves, cardio, chest, legs, shoulders)")):
    """d) Conta as menções a equipamento nas descrições de uma categoria."""
    filtros = filtro_de(categoria)
    if "category" not in filtros:
        raise typer.BadParameter(f"'{categoria}' é um músculo; aqui é preciso uma categoria.")
    textos = []
    for ex in exercicios(filtros):
        textos.append(limpar_html(traducao_inglesa(ex)["description"]))
    escrever_json(contar_equipamento(textos))


@app.command()
def workout(
    lista: str = typer.Argument(..., help="Músculos ou categorias separados por vírgula (ex.: biceps,back)"),
    esforco: str = typer.Argument("any", help="compound, isolated ou any"),
    n: int = typer.Argument(5, min=1, help="Máximo de exercícios por grupo"),
    csv_saida: str = typer.Option(None, "--csv", help="Exportar o plano para este ficheiro CSV"),
):
    """e) Plano de treino: junta o search, o type e o equipment."""
    esforco = esforco.lower()
    if esforco not in ["compound", "isolated", "any"]:  # testa-se antes de fazer pedidos à API
        raise typer.BadParameter("O esforço tem de ser compound, isolated ou any.")

    plano = {}
    for grupo in lista.split(","):
        grupo = grupo.strip()
        if grupo == "":
            continue
        escolhidos = []
        for ex in exercicios(filtro_de(grupo)):
            t = tipo_esforco(ex)
            if esforco == "any" or t == esforco:
                linha = resumo(ex)
                linha["type"] = t
                escolhidos.append(linha)
            if len(escolhidos) == n:
                break
        if len(escolhidos) == 0:  # vai para o stderr, para não estragar o JSON
            typer.echo(f"Sem resultados para '{grupo}'.", err=True)
        plano[grupo] = escolhidos

    if csv_saida:
        linhas = 0
        # utf-8-sig e ";" para o Excel em português abrir bem os acentos e as colunas
        with open(csv_saida, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["group", "id", "name", "type", "category", "equipment", "muscles"])
            for grupo in plano:
                for e in plano[grupo]:
                    w.writerow([grupo, e["id"], e["name"], e["type"], e["category"],
                                ", ".join(e["equipment"]), ", ".join(e["muscles"])])
                    linhas += 1
        typer.echo(f"Ficheiro exportado: {csv_saida} ({linhas} registos)")
    else:
        escrever_json(plano)


@app.command()
def grupos():
    """Extra: mostra os músculos e as categorias que se podem usar."""
    categorias = []
    for c in pedido("exercisecategory/")["results"]:
        categorias.append(c["name"].lower())
    nomes_musculos = []
    for m in pedido("muscle/", {"limit": 100})["results"]:
        if m["name_en"]:
            nomes_musculos.append(m["name_en"].lower())
        else:
            nomes_musculos.append(m["name"].lower())
    escrever_json({"categorias": categorias, "musculos": nomes_musculos})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # para os acentos aparecerem bem no terminal do Windows
    sys.stderr.reconfigure(encoding="utf-8")
    app()

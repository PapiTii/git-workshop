"""fitcli — CLI para explorar a API wger (Workout Manager).

TP1 de ALPCD 2026/27 · módulos requests, re e typer.

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
from enum import Enum
from functools import lru_cache

import requests
import typer

# ---------------------------------------------------------------------------
# 1. Ligação à API
# ---------------------------------------------------------------------------

BASE = "https://wger.de/api/v2/"
CABECALHOS = {"User-Agent": "fitcli/1.0 (TP1 ALPCD - Universidade do Minho)"}
INGLES = 2  # id da língua inglesa na wger

app = typer.Typer(help="Pesquisa exercícios físicos na API wger.")

sessao = requests.Session()  # reaproveita a mesma ligação em todos os pedidos
sessao.headers.update(CABECALHOS)


def pedido(rota, params=None):
    """Faz um GET à API e devolve o JSON. Pára o programa se o status não for 200."""
    url = rota if rota.startswith("http") else BASE + rota
    try:
        r = sessao.get(url, params=params, timeout=20)
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


def paginas(rota, params=None):
    """Percorre todas as páginas de uma rota, seguindo o campo 'next'.

    É um gerador: devolve um resultado de cada vez, e só pede a página
    seguinte quando é preciso. Assim, quem só quer 5 resultados não
    descarrega os 900 exercícios.
    """
    dados = pedido(rota, params)
    while True:
        yield from dados["results"]
        if not dados["next"]:
            break
        dados = pedido(dados["next"])  # o 'next' já traz os parâmetros todos


# ---------------------------------------------------------------------------
# 2. Tratar os dados de um exercício
# ---------------------------------------------------------------------------

def traducao_inglesa(ex):
    """Devolve a tradução em inglês de um exercício (ou None se não houver)."""
    for t in ex.get("translations", []):
        if t["language"] == INGLES:
            return t
    return None


def limpar_html(texto):
    """Tira as etiquetas HTML da descrição com expressões regulares."""
    texto = re.sub(r"<[^>]+>", " ", texto or "")   # <p>, <li>, </ol> ... → espaço
    texto = html.unescape(texto)                    # &amp; → &
    return re.sub(r"\s+", " ", texto).strip()       # espaços repetidos → um só


def musculos(ex):
    """Nomes dos músculos (principais e secundários), preferindo o nome comum."""
    lista = ex["muscles"] + ex["muscles_secondary"]
    return [m["name_en"] or m["name"] for m in lista]


def resumo(ex):
    """O dicionário que se mostra ao utilizador."""
    return {
        "id": ex["id"],
        "name": ex["_en"]["name"],
        "category": ex["category"]["name"],
        "equipment": [e["name"] for e in ex["equipment"]],
        "muscles": musculos(ex),
    }


def exercicios(params, maximo=None):
    """Exercícios em inglês que cumprem os filtros, até 'maximo' (None = todos)."""
    limite = min(100, maximo) if maximo else 100  # top 3 não precisa de descarregar 100
    params = {"language": INGLES, "limit": limite, **params}
    contados = 0
    for ex in paginas("exerciseinfo/", params):
        t = traducao_inglesa(ex)
        if t is None:  # há exercícios só noutras línguas
            continue
        ex["_en"] = t  # calculada uma vez; resumo e tipo_esforco usam ex["_en"]
        yield ex
        contados += 1
        if maximo is not None and contados >= maximo:
            return


# ---------------------------------------------------------------------------
# 3. Músculos e categorias: converter o nome escrito num filtro da API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)  # cada lista é pedida à API uma só vez por execução
def lista_musculos():
    return pedido("muscle/", {"limit": 100})["results"]


@lru_cache(maxsize=None)
def lista_categorias():
    return pedido("exercisecategory/")["results"]


def normalizar(s):
    return re.sub(r"[^a-z]", "", s.lower())  # "Biceps brachii" → "bicepsbrachii"


def filtro_de(nome):
    """Converte 'biceps' ou 'arms' no filtro da API (muscles=1 ou category=8)."""
    erro = typer.BadParameter(f"'{nome}' não é um músculo nem uma categoria. Veja: python fitcli.py grupos")
    alvo = normalizar(nome)
    if not alvo:  # "123" fica vazio, e "" iria corresponder a qualquer músculo
        raise erro
    for c in lista_categorias():
        if normalizar(c["name"]) == alvo:
            return {"category": c["id"]}
    for m in lista_musculos():
        if alvo in (normalizar(m["name"]), normalizar(m["name_en"] or "")):
            return {"muscles": m["id"]}
    # sem correspondência exata: aceita o início do nome ("pector" → Pectoralis major)
    for m in lista_musculos():
        if normalizar(m["name"]).startswith(alvo) or normalizar(m["name_en"] or "").startswith(alvo):
            return {"muscles": m["id"]}
    raise erro


# ---------------------------------------------------------------------------
# 4. Tipo de esforço (alínea c)
# ---------------------------------------------------------------------------

# Palavras que denunciam o tipo de exercício, procuradas no nome e na descrição.
# \b = fronteira de palavra; s? = plural opcional; (?:...) = grupo sem captura.
CARDIO = re.compile(r"\b(?:run(?:ning)?|jog(?:ging)?|sprint|cycl(?:e|ing)|bike|row(?:ing)? machine|"
                    r"jump(?:ing)? jacks?|burpees?|skipping|jump rope|treadmill|elliptical|swim(?:ming)?)\b", re.I)
COMPOSTO = re.compile(r"\b(?:squats?|deadlifts?|bench press|press(?:es)?|rows?|pull[- ]?ups?|chin[- ]?ups?|"
                      r"push[- ]?ups?|dips?|lunges?|cleans?|snatch|thrusters?|step[- ]?ups?|hip thrusts?)\b", re.I)
ISOLADO = re.compile(r"\b(?:curls?|raises?|fl(?:y|ies|yes)|extensions?|kickbacks?|shrugs?|crunch(?:es)?|"
                     r"pullovers?|pushdowns?|calf|wrist)\b", re.I)


def tipo_esforco(ex):
    """compound / isolated / cardio / other, por esta ordem de regras:

    1. categoria Cardio, ou palavra de cardio no nome → cardio
    2. palavra de exercício isolado no NOME → isolated   (ex.: "Leg Curl")
    3. palavra de exercício composto no NOME → compound  (ex.: "Barbell Row")
    4. 3 ou mais músculos envolvidos → compound (mexe várias articulações)
    5. 1 só músculo → isolated
    6. o mesmo teste das palavras, mas na descrição
    7. nada disto → other
    O nome e o número de músculos vêm antes da descrição porque são mais
    fiáveis: uma descrição de um 'curl' pode dizer "stand as in a squat", e
    "press" ou "row" aparecem em frases normais. Consequência: a descrição só
    decide quando o exercício tem 0 ou 2 músculos.
    """
    nome, descricao = ex["_en"]["name"], limpar_html(ex["_en"]["description"])
    n_musculos = len(ex["muscles"]) + len(ex["muscles_secondary"])

    if ex["category"]["name"] == "Cardio" or CARDIO.search(nome):
        return "cardio"
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
    "bar": r"\b(?<!up )(?:ez[- ]?)?bars?\b",  # "bar", "EZ bar"; (?<!up ) não volta a contar "pull-up bar"
    "plate": r"\bplates?\b",
    "rope": r"\bropes?\b",
    "roller": r"\brollers?\b",
    "mat": r"\bmats?\b",
}


def contar_equipamento(textos):
    """Conta as menções a cada equipamento e ordena do mais falado para o menos."""
    contagem = {nome: 0 for nome in EQUIPAMENTO}
    for texto in textos:
        for nome, padrao in EQUIPAMENTO.items():
            contagem[nome] += len(re.findall(padrao, texto, re.I))
    ordenado = sorted(contagem.items(), key=lambda par: par[1], reverse=True)
    return [{nome: n} for nome, n in ordenado if n > 0]


# ---------------------------------------------------------------------------
# 6. Comandos da CLI
# ---------------------------------------------------------------------------

class Esforco(str, Enum):
    compound = "compound"
    isolated = "isolated"
    any = "any"


def escrever_json(dados):
    typer.echo(json.dumps(dados, ensure_ascii=False, indent=2))


@app.command()
def top(n: int = typer.Argument(10, min=1, help="Quantos exercícios mostrar")):
    """a) Os N exercícios mais recentes da base de dados."""
    escrever_json([resumo(ex) for ex in exercicios({"ordering": "-created"}, n)])


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
    escrever_json([resumo(ex) for ex in exercicios(filtros, n)])


@app.command("type")
def tipo(exercise_id: int = typer.Argument(..., help="O id do exercício (ver no 'top' ou no 'search')")):
    """c) Tipo de esforço de um exercício: compound, isolated, cardio ou other."""
    ex = pedido(f"exerciseinfo/{exercise_id}/")
    ex["_en"] = traducao_inglesa(ex)
    if ex["_en"] is None:
        typer.echo("Este exercício não tem versão em inglês.", err=True)
        raise typer.Exit(1)
    typer.echo(tipo_esforco(ex))


@app.command()
def equipment(categoria: str = typer.Argument(..., help="Categoria (abs, arms, back, calves, cardio, chest, legs, shoulders)")):
    """d) Conta as menções a equipamento nas descrições de uma categoria."""
    filtros = filtro_de(categoria)
    if "category" not in filtros:
        raise typer.BadParameter(f"'{categoria}' é um músculo; aqui é preciso uma categoria.")
    textos = [limpar_html(ex["_en"]["description"]) for ex in exercicios(filtros)]
    escrever_json(contar_equipamento(textos))


@app.command()
def workout(
    lista: str = typer.Argument(..., help="Músculos ou categorias separados por vírgula (ex.: biceps,back)"),
    esforco: Esforco = typer.Argument(Esforco.any, case_sensitive=False, help="Tipo de esforço"),
    n: int = typer.Argument(5, min=1, help="Máximo de exercícios por grupo"),
    csv_saida: str = typer.Option(None, "--csv", help="Exportar o plano para este ficheiro CSV"),
):
    """e) Plano de treino: junta o search, o type e o equipment."""
    plano = {}
    for grupo in [g.strip() for g in lista.split(",") if g.strip()]:
        escolhidos = []
        for ex in exercicios(filtro_de(grupo)):
            t = tipo_esforco(ex)
            if esforco == Esforco.any or t == esforco.value:
                escolhidos.append({**resumo(ex), "type": t})
            if len(escolhidos) == n:
                break
        if not escolhidos:  # vai para o stderr, para não estragar o JSON
            typer.echo(f"Sem resultados para '{grupo}'.", err=True)
        plano[grupo] = escolhidos

    if csv_saida:
        linhas = 0
        with open(csv_saida, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: o Excel lê bem os acentos
            w = csv.writer(f, delimiter=";")  # o Excel português usa ;
            w.writerow(["group", "id", "name", "type", "category", "equipment", "muscles"])
            for grupo, lista in plano.items():
                for e in lista:
                    w.writerow([grupo, e["id"], e["name"], e["type"], e["category"],
                                ", ".join(e["equipment"]), ", ".join(e["muscles"])])
                    linhas += 1
        typer.echo(f"Ficheiro exportado: {csv_saida} ({linhas} registos)")
    else:
        escrever_json(plano)


@app.command()
def grupos():
    """Extra: mostra os músculos e as categorias que se podem usar."""
    escrever_json({
        "categorias": [c["name"].lower() for c in lista_categorias()],
        "musculos": [(m["name_en"] or m["name"]).lower() for m in lista_musculos()],
    })


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # para os acentos saírem bem no terminal do Windows
    app()

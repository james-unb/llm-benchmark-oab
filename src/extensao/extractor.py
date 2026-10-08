"""Extração de enunciados, alternativas e gabaritos a partir dos PDFs locais."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

import pymupdf

# Distribuição indicada no plano do projeto para cadernos de 80 questões.
# Provas antigas de 100 questões não recebem matéria por posição: a ordem
# histórica precisa de um mapa próprio validado antes de preencher esse campo.
FAIXAS_MATERIA_80 = (
    (1, 8, "Ética Profissional"),
    (9, 10, "Filosofia do Direito"),
    (11, 16, "Direito Constitucional"),
    (17, 18, "Direitos Humanos"),
    (19, 20, "Direito Eleitoral"),
    (21, 22, "Direito Internacional"),
    (23, 24, "Direito Financeiro"),
    (25, 29, "Direito Tributário"),
    (30, 34, "Direito Administrativo"),
    (35, 36, "Direito Ambiental"),
    (37, 42, "Direito Civil"),
    (43, 44, "Estatuto da Criança e do Adolescente"),
    (45, 46, "Direito do Consumidor"),
    (47, 50, "Direito Empresarial"),
    (51, 56, "Processo Civil"),
    (57, 62, "Direito Penal"),
    (63, 68, "Processo Penal"),
    (69, 70, "Direito Previdenciário"),
    (71, 75, "Direito do Trabalho"),
    (76, 80, "Processo do Trabalho"),
)

def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


@lru_cache(maxsize=1024)
def _texto_pdf(caminho: Path) -> str:
    """Extrai texto e usa OCR quando a fonte do PDF contém mapa de caracteres corrompido."""
    with pymupdf.open(caminho) as documento:
        paginas = list(documento)
        textos: list[str] = []
        motor = None
        numpy = None
        for pagina in paginas:
            texto = pagina.get_text(sort=False)
            nao_latin = sum(
                1 for caractere in texto
                if (unicodedata.category(caractere).startswith("L")
                    and not unicodedata.name(caractere, "").startswith("LATIN"))
                or (ord(caractere) < 32 and caractere not in "\n\r\t")
            )
            corrompido = bool(texto) and nao_latin / len(texto) > 0.025
            if corrompido:
                try:
                    if motor is None:
                        import numpy as np
                        from rapidocr import RapidOCR
                        numpy = np
                        motor = RapidOCR()
                    pixmap = pagina.get_pixmap(dpi=200, alpha=False)
                    imagem = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(
                        pixmap.height, pixmap.width, pixmap.n
                    )
                    resultado = motor(imagem)
                    texto = "\n".join(resultado.txts or [])
                except ImportError:
                    pass
            textos.append(texto)
        return "\n".join(textos)


def _numero_exame(nome_pasta: str) -> str:
    chave = re.sub(r"[_-]+", " ", _normalizar(nome_pasta))
    antigo = re.search(r"2010\s*\.?\s*(2|3)", chave)
    if antigo:
        return f"2010.{antigo.group(1)}"
    numerico = re.match(r"\s*(\d{1,2})(?:º|o)?(?=\s|$)", chave)
    if numerico:
        return str(int(numerico.group(1)))
    romano = re.search(
        r"\b(xxxviii|xxxvii|xxxvi|xxxv|xxxiv|xxxiii|xxxii|xxxi|xxx|xxix|xxviii|xxvii|xxvi|xxv|xxiv|xxiii|xxii|xxi|xx|xix|xviii|xvii|xvi|xv|xiv|xiii|xii|xi|ix|viii|vii|vi|iv|iii|ii|x|v|i)\b",
        chave,
    )
    if romano:
        return str(_ordinal_exame(romano.group(1).upper()))
    ordinal = re.search(r"\b(\d{1,2})\s*o?\b", chave)
    return str(int(ordinal.group(1))) if ordinal else nome_pasta


def _ordinal_exame(numero: str) -> int | None:
    if numero.isdigit():
        return int(numero)
    simbolos = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    romano = numero.upper()
    if not romano or any(c not in simbolos for c in romano):
        return None
    total = 0
    for i, char in enumerate(romano):
        valor = simbolos[char]
        total += -valor if i + 1 < len(romano) and valor < simbolos[romano[i + 1]] else valor
    return total


def _data_exame(numero_exame: str, arquivos: list[Path]) -> str | None:
    for arquivo in arquivos:
        # Nomes atuais usam ddmmyyyy; alguns arquivos mais antigos, dd.mm.yyyy.
        m = re.search(r"(?<!\d)(\d{2})[._\-/ ]?(\d{2})[._\-/ ]?(20\d{2})(?!\d)", arquivo.name)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
            except ValueError:
                continue
    return None


def _classificar_chave(nome: str, texto: str = "") -> str | None:
    n = _normalizar(nome)
    if "gabarito" not in n:
        return None

    # A classificação publicada na página oficial determina o tipo do arquivo.
    # Alguns PDFs listados como preliminares têm "GABARITOS DEFINITIVOS" no
    # título interno; não os promovemos a definitivos por causa dessa divergência.
    if "preliminar" in n:
        return "preliminar"
    if any(p in n for p in ("definitiv", "final", "apos recurso", "apos recursos")):
        return "definitivo"

    titulo = _normalizar(texto[:3000])
    if re.search(r"gabaritos? definitivos?", titulo):
        return "definitivo"
    if re.search(r"gabaritos? preliminar(?:es)?", titulo):
        return "preliminar"
    return "oficial"


def _tipo_no_documento(texto: str, nome: str) -> int:
    combinado = _normalizar(f"{nome}\n{texto[:1800]}")
    m = re.search(r"(?:prova\s*)?tipo\s*[-º°]?\s*(\d)", combinado)
    if m:
        return int(m.group(1))
    m = re.search(r"caderno\s+(?:de\s+)?prova\s+(?:n[ºo.]?\s*)?0?(\d)", combinado)
    if m:
        return int(m.group(1))
    return 1


def _isolar_tipo_1(texto: str) -> str:
    # Gabaritos recentes contêm quatro tabelas, uma por tipo de prova.
    marcador = re.search(r"(?im)^\s*[^\n]*\bexame\b[^\n]*\btipo\s+1\b[^\n]*", texto)
    if marcador:
        inicio = texto.find("\n", marcador.end())
        depois = texto[inicio + 1:] if inicio >= 0 else ""
        proximo = re.search(r"(?im)^\s*[^\n]*\bexame\b[^\n]*\btipo\s+[234]\b[^\n]*", depois)
        if proximo:
            return depois[:proximo.start()]
        return depois
    return texto


def _parse_gabarito(caminho: Path) -> dict[int, str]:
    texto = _texto_pdf(caminho)
    tipo_no_nome = _tipo_no_documento(texto, caminho.name)
    if tipo_no_nome != 1:
        return {}
    texto = _isolar_tipo_1(texto)
    respostas: dict[int, str] = {}
    # Alguns gabaritos antigos cruzam a numeração dos quatro tipos em uma tabela.
    texto_n = _normalizar(texto)
    cabecalhos = list(re.finditer(
        r"tipo\s*1\s+tipo\s*2\s+tipo\s*3\s+tipo\s*4\s+gabarito\b", texto_n
    ))
    if cabecalhos:
        for indice, cabecalho in enumerate(cabecalhos):
            fim = cabecalhos[indice + 1].start() if indice + 1 < len(cabecalhos) else len(texto_n)
            trecho = texto_n[cabecalho.end():fim]
            tokens_tabela = re.findall(r"(?i)\b(?:\d{1,3}|anulada|anulado|[a-d]|x|\*)\b", trecho)
            for i in range(0, len(tokens_tabela) - 4, 5):
                grupo = tokens_tabela[i:i + 5]
                if grupo[0].isdigit() and all(valor.isdigit() for valor in grupo[1:4]) and not grupo[4].isdigit():
                    numero = int(grupo[0])
                    if 1 <= numero <= 100:
                        respostas[numero] = _normalizar_resposta(grupo[4])
        if len(respostas) >= 70:
            return respostas
    # O gabarito 2010.2 lista cada par "número – resposta".
    pares = re.findall(
        r"(?i)(?<![a-z0-9])0*(\d{1,3})\s*[–—-]\s*(ANULADA|ANULADO|[A-D]|X|\*)(?![a-z0-9])",
        texto,
    )
    if len(pares) >= 80:
        return {int(n): _normalizar_resposta(r) for n, r in pares if 1 <= int(n) <= 100}
    # Em vários gabaritos antigos, dezenas de números são impressas em uma
    # única linha; tokenizar o texto dá suporte aos dois layouts.
    tokens = re.findall(r"(?i)(?<![a-z0-9])(?:\d{1,3}|ANULADA|ANULADO|[A-D]|X|\*)(?![a-z0-9])", texto)
    i = 0
    while i < len(tokens):
        if not tokens[i].isdigit():
            i += 1
            continue
        numeros: list[int] = []
        j = i
        while j < len(tokens) and tokens[j].isdigit():
            n = int(tokens[j])
            if n < 1 or n > 120 or (numeros and n != numeros[-1] + 1):
                break
            numeros.append(n)
            j += 1
        if len(numeros) >= 5:
            respostas_possiveis = tokens[j:j + len(numeros)]
            if len(respostas_possiveis) == len(numeros) and all(not t.isdigit() for t in respostas_possiveis):
                for n, resposta in zip(numeros, respostas_possiveis):
                    respostas[n] = _normalizar_resposta(resposta)
                i = j + len(numeros)
                continue
        i += 1
    return respostas


def _normalizar_resposta(valor: str) -> str:
    valor = _normalizar(valor).upper()
    return "ANULADA" if valor in {"X", "*", "ANULADA", "ANULADO"} else valor


def _questoes(texto: str, limite_questao: int = 80) -> list[dict[str, object]]:
    # Os cadernos de 2010 têm 100 itens e os mais recentes, 80. A sequência é
    # descoberta no próprio PDF, sem limitar o parser a uma quantidade fixa.
    marcadores: list[tuple[int, int]] = []
    ultimo = 0
    for m in re.finditer(r"(?im)^\s*(?:quest[ãa]o\s+)?(\d{1,3})(?:\s+A\d{4,6})?\s*$", texto):
        n = int(m.group(1))
        # Ignora o número da página que aparece isolado antes da questão 1.
        if ultimo == 0 and n != 1:
            continue
        # Alguns PDFs omitem o texto de até três questões (por exemplo, uma
        # página digitalizada); aceitar um pequeno salto preserva as seguintes.
        if ultimo < n <= limite_questao and (ultimo == 0 or n <= ultimo + 3):
            marcadores.append((n, m.end()))
            ultimo = n
    saida: list[dict[str, object]] = []
    anterior = 0
    for idx, (numero, inicio) in enumerate(marcadores):
        for ausente in range(anterior + 1, numero):
            saida.append({
                "numero_questao": ausente, "enunciado": None,
                **{f"alternativa_{letra.lower()}": None for letra in "ABCD"},
                "questao_extraida": False,
            })
        fim = marcadores[idx + 1][1] if idx + 1 < len(marcadores) else len(texto)
        bloco = texto[inicio:fim].strip()
        # O marcador seguinte está incluído no início do próximo bloco; remove
        # o número e cabeçalhos de página antes de separar o enunciado.
        bloco = re.sub(r"(?m)^\s*\d{1,3}\s*$", "", bloco)
        bloco = re.sub(r"(?im)^\s*\d{1,2}\s*o?\s+exame[^\n]*$", "", bloco)
        bloco = re.sub(r"(?im)^\s*tipo\s+branca[^\n]*$", "", bloco)
        bloco = re.sub(r"(?im)^\s*pagina\s+\d+\s*$", "", bloco)
        opcoes = list(re.finditer(r"(?m)^\s*(?:\(([A-D])\)|([A-D])[.)])\s*", bloco))
        enunciado = bloco[:opcoes[0].start()].strip() if opcoes else bloco.strip()
        alternativas: dict[str, str | None] = {letra: None for letra in "ABCD"}
        for i, opcao in enumerate(opcoes):
            letra = opcao.group(1) or opcao.group(2)
            fim_opcao = opcoes[i + 1].start() if i + 1 < len(opcoes) else len(bloco)
            alternativas[letra] = bloco[opcao.end():fim_opcao].strip()
        saida.append({
            "numero_questao": numero,
            "enunciado": _limpar(enunciado),
            "questao_extraida": bool(
                enunciado.strip() and all(alternativas.get(letra) for letra in "ABCD")
            ),
            **{f"alternativa_{k.lower()}": _limpar(v) if v else None for k, v in alternativas.items()},
        })
        anterior = numero
    return saida


def _limpar(texto: str | None) -> str | None:
    if not texto:
        return None
    # Mantém o caractere de substituição visível e sinaliza a linha para QA;
    # não inventa acentos quando a fonte não fornece o mapeamento Unicode.
    texto = texto.replace("\ufffd", "�")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r" *\n *", " ", texto)
    return texto.strip()


def _materia(numero: int, quantidade: int) -> str | None:
    if quantidade != 80:
        return None
    for inicio, fim, nome in FAIXAS_MATERIA_80:
        if inicio <= numero <= fim:
            return nome
    return None


def extrair_provas(pasta_provas: Path) -> list[dict[str, object]]:
    registros: list[dict[str, object]] = []
    for pasta in sorted(p for p in pasta_provas.iterdir() if p.is_dir()):
        arquivos = sorted(pasta.glob("*.pdf"))
        provas = [p for p in arquivos if _tipo_no_documento("", p.name) == 1 and _eh_prova_tipo_1(p.name)]
        if not provas:
            continue
        numero_exame = _numero_exame(pasta.name)
        data_exame = _data_exame(numero_exame, provas)
        chaves: dict[str, dict[int, str]] = defaultdict(dict)
        caminhos_chave: dict[str, str | None] = {"preliminar": None, "definitivo": None, "oficial": None}
        comunicado_manteve_gabarito: Path | None = None
        for arquivo in arquivos:
            nome_normalizado = _normalizar(arquivo.name)
            if "comunicado" in nome_normalizado:
                texto_comunicado = " ".join(_normalizar(_texto_pdf(arquivo)).split())
                if "mantido integralmente o gabarito publicado" in texto_comunicado:
                    comunicado_manteve_gabarito = arquivo
                continue
            if "gabarito" not in nome_normalizado:
                continue
            texto_chave = _texto_pdf(arquivo)
            classe = _classificar_chave(arquivo.name, texto_chave)
            if classe:
                respostas = _parse_gabarito(arquivo)
                if respostas:
                    chaves[classe].update(respostas)
                    caminhos_chave[classe] = arquivo.as_posix()

        prova = provas[0]
        texto = _texto_pdf(prova)
        limite_questao = 100 if numero_exame in {"2010.2", "2010.3"} else 80
        questoes = _questoes(texto, limite_questao=limite_questao)
        if not questoes:
            print(f"AVISO: nenhuma questão extraída de {prova}")
            continue
        # Um gabarito oficial único de páginas antigas funciona como resposta
        # definitiva somente quando a fonte o identifica como oficial e não há
        # documento preliminar nem definitivo separado.
        prelim = chaves.get("preliminar", {}) or chaves.get("oficial", {})
        final = chaves.get("definitivo", {})
        if not caminhos_chave["preliminar"] and caminhos_chave["oficial"]:
            caminhos_chave["preliminar"] = caminhos_chave["oficial"]
        # Se a OAB/FGV comunica expressamente que o gabarito foi mantido
        # integralmente após os recursos, essa publicação é a evidência do
        # definitivo, mesmo sem um segundo PDF de respostas.
        if not final and prelim and comunicado_manteve_gabarito:
            final = dict(prelim)
            caminhos_chave["definitivo"] = comunicado_manteve_gabarito.as_posix()
        anuladas_por_comunicado: set[int] = set()
        for comunicado in arquivos:
            nome_comunicado = _normalizar(comunicado.name)
            if "comunicado" not in nome_comunicado:
                continue
            texto_comunicado = " ".join(
                _normalizar(_texto_pdf(comunicado)).split()
            )
            if "anula" not in nome_comunicado and "anula" not in texto_comunicado:
                continue
            # Comunicados antigos costumam informar várias questões no corpo
            # do PDF, sem enumerá-las no nome do arquivo. Limitamos a busca ao
            # trecho após "questões" e capturamos somente uma lista explícita
            # de números, sem incluir depois o número do caderno/tipo.
            trechos = [texto_comunicado, nome_comunicado]
            for trecho in trechos:
                for match in re.finditer(
                    r"quest[^\s]{0,6}\s*(?:de\s+)?(?:n(?:umero|[^\s]{1,3})\s*)?"
                    r"(?P<numeros>\d{1,3}(?:\s*(?:,|e)\s*\d{1,3})*)",
                    trecho,
                    re.I,
                ):
                    anuladas_por_comunicado.update(
                        int(numero)
                        for numero in re.findall(r"\d{1,3}", match.group("numeros"))
                        if 1 <= int(numero) <= limite_questao
                    )

        total = max(
            len(questoes),
            max(prelim.keys(), default=0),
            max(final.keys(), default=0),
        )
        # A lista de arquivos já selecionou o PDF identificado como caderno tipo 1.
        tipo_prova = 1
        for q in questoes:
            n = int(q["numero_questao"])
            preliminar = prelim.get(n)
            definitiva_fonte = final.get(n)
            if definitiva_fonte is None and chaves.get("oficial"):
                definitiva_fonte = chaves["oficial"].get(n)
            anulada_confirmada = (
                preliminar == "ANULADA"
                or definitiva_fonte == "ANULADA"
                or n in anuladas_por_comunicado
            )
            if anulada_confirmada:
                definitiva = "ANULADA"
            elif definitiva_fonte is not None:
                definitiva = definitiva_fonte
            else:
                definitiva = None
            fonte_relativa = _fonte_relativa(prova)
            fonte_preliminar = _fonte_relativa(Path(caminhos_chave["preliminar"])) if caminhos_chave["preliminar"] else None
            fonte_definitivo = _fonte_relativa(Path(caminhos_chave["definitivo"])) if caminhos_chave["definitivo"] else None
            registros.append({
                "numero_exame": numero_exame,
                "data_exame": data_exame,
                "tipo_prova": tipo_prova,
                "numero_questao": n,
                "materia": _materia(n, total) if numero_exame.isdigit() and int(numero_exame) >= 16 else None,
                **{k: q[k] for k in ("enunciado", "alternativa_a", "alternativa_b", "alternativa_c", "alternativa_d")},
                "questao_extraida": bool(q.get("questao_extraida")),
                "resposta_preliminar": preliminar,
                "resposta_definitiva": definitiva,
                "fonte_prova": fonte_relativa,
                "fonte_gabarito_preliminar": fonte_preliminar,
                "fonte_gabarito_definitivo": fonte_definitivo,
                "gabarito_definitivo": "true" if definitiva is not None else "false",
            })
    return registros


def _eh_prova_tipo_1(nome: str) -> bool:
    n = _normalizar(nome)
    if "gabarito" in n or "comunicado" in n:
        return False
    if re.search(r"\btipo\s*[-º°]?\s*[234]\b", n):
        return False
    if re.search(r"\bcaderno\s+de\s+prova\s+0?[234]\b", n):
        return False
    return bool(
        re.search(r"\btipo\s*[-º°]?\s*1\b", n)
        or re.search(r"\bcaderno\s+(?:de\s+)?prova\s+(?:n[ºo.]?\s*)?0?1\b", n)
    )


def _fonte_relativa(caminho: Path) -> str:
    caminho = caminho.resolve()
    try:
        return caminho.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return (Path("provas_oab") / caminho.parent.name / caminho.name).as_posix()

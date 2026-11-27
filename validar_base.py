"""Audita as três exportações da base OAB sem modificar dados ou executar SQL do dump."""
import argparse
import csv
import io
import re
import sqlite3
import subprocess
import sys
import unicodedata
from collections import Counter
from contextlib import closing
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CAMPOS = (
    'numero_exame', 'data_exame', 'tipo_prova', 'numero_questao', 'materia',
    'enunciado', 'alternativa_a', 'alternativa_b', 'alternativa_c', 'alternativa_d',
    'resposta_preliminar', 'resposta_definitiva', 'questao_extraida', 'fonte_prova',
    'fonte_gabarito_preliminar', 'fonte_gabarito_definitivo', 'gabarito_definitivo',
)
TEXTOS = CAMPOS[5:10]
ANTIGOS = {'2010.2', '2010.3'}
INDICADORES = {
    'total': 'Questões cadastradas',
    'definitivo': 'Elegíveis (definitivo)',
    'preliminar': 'Elegíveis (inclui preliminar)',
    'nao_extraida': 'Questões não extraídas',
    'sem_materia': 'Sem classificação da matéria',
    'marcador': 'Marcador da questão seguinte',
    'cabecalho': 'Possível cabeçalho residual',
    'd_longa': 'Alternativa D desproporcional',
    'identicas': 'Alternativas idênticas',
    'questionario': 'Possível questionário de percepção',
    'vizinha_ausente': 'Questão extraída seguida de questão ausente',
}
ALERTAS = tuple(INDICADORES)[3:]
TOKEN = r"(?:'(?:[^']|'')*'|NULL|-?\d+)"
INSERT = re.compile(r'INSERT INTO "questoes_oab" VALUES\((' + TOKEN + r'(?:,' + TOKEN + r')*)\);', re.S)
DDL = '''CREATE TABLE questoes_oab (
    id INTEGER PRIMARY KEY,
    numero_exame TEXT NOT NULL,
    data_exame TEXT,
    tipo_prova INTEGER NOT NULL CHECK (tipo_prova = 1),
    numero_questao INTEGER NOT NULL,
    materia TEXT,
    enunciado TEXT,
    alternativa_a TEXT,
    alternativa_b TEXT,
    alternativa_c TEXT,
    alternativa_d TEXT,
    resposta_preliminar TEXT,
    resposta_definitiva TEXT,
    questao_extraida INTEGER NOT NULL CHECK (questao_extraida IN (0, 1)),
    fonte_prova TEXT NOT NULL,
    fonte_gabarito_preliminar TEXT,
    fonte_gabarito_definitivo TEXT,
    gabarito_definitivo TEXT NOT NULL CHECK (gabarito_definitivo IN ('true', 'false')),
    UNIQUE (numero_exame, tipo_prova, numero_questao)
);'''
DDL_RESTANTE = (
    'CREATE INDEX idx_questoes_oab_exame ON questoes_oab(numero_exame, numero_questao);',
    'CREATE INDEX idx_questoes_oab_materia ON questoes_oab(materia);',
    '''CREATE VIEW questoes_llm AS
SELECT * FROM questoes_oab
WHERE questao_extraida = 1 AND enunciado IS NOT NULL
  AND resposta_definitiva IS NOT NULL
  AND resposta_definitiva <> 'ANULADA'
  AND gabarito_definitivo = 'true';''',
)


def chave(q):
    return q['numero_exame'], q['tipo_prova'], q['numero_questao']


def ordem(k):
    return tuple(map(int, k[0].split('.'))), int(k[1]), int(k[2])


def rotulo(k):
    return f'{k[0]}/Q{k[2]}'


def ler_csv(texto):
    leitor = csv.DictReader(io.StringIO(texto, newline=''), strict=True)
    if leitor.fieldnames != list(CAMPOS):
        raise ValueError('Cabeçalho CSV incompatível com o esquema esperado.')
    linhas = []
    for q in leitor:
        if None in q or any(v is None for v in q.values()):
            raise ValueError(f'CSV: quantidade de campos incorreta na linha {leitor.line_num}.')
        linhas.append({k: v.replace('\r\n', '\n').replace('\r', '\n') for k, v in q.items()})
    return linhas


def converter_bd(valores):
    if len(valores) != len(CAMPOS):
        raise ValueError('Número incorreto de colunas no banco.')
    q = dict(zip(CAMPOS, valores))
    for k in ('tipo_prova', 'numero_questao', 'questao_extraida'):
        if type(q[k]) is not int:
            raise ValueError(f'Banco: {k} não é inteiro.')
    if q['questao_extraida'] not in (0, 1):
        raise ValueError('Banco: questao_extraida inválida.')
    q['questao_extraida'] = str(bool(q['questao_extraida']))
    for k in ('tipo_prova', 'numero_questao'):
        q[k] = str(q[k])
    for k, v in q.items():
        if v is None:
            q[k] = ''  # NULL no banco equivale ao campo vazio do CSV.
        elif not isinstance(v, str):
            raise ValueError(f'Banco: tipo inesperado em {k}.')
        else:
            q[k] = v.replace('\r\n', '\n').replace('\r', '\n')
    return q


def ler_sql(texto):
    """Aceita apenas o formato do dump atual; não executa nenhuma instrução."""
    texto = texto.strip()
    prefixo = 'BEGIN TRANSACTION;\n' + DDL
    if not texto.startswith(prefixo):
        raise ValueError('SQL: esquema inicial inesperado.')
    pos = len(prefixo)
    linhas, ids = [], set()
    while True:
        while pos < len(texto) and texto[pos].isspace():
            pos += 1
        m = INSERT.match(texto, pos)
        if not m:
            break
        valores = []
        for token in re.findall(TOKEN, m.group(1)):
            valores.append(None if token == 'NULL' else token[1:-1].replace("''", "'")
                           if token.startswith("'") else int(token))
        if type(valores[0]) is not int or valores[0] in ids:
            raise ValueError('SQL: ID inválido ou duplicado.')
        ids.add(valores[0])
        linhas.append(converter_bd(valores[1:]))
        pos = m.end()
    if texto[pos:] != '\n'.join((*DDL_RESTANTE, 'COMMIT;')):
        raise ValueError('SQL: instrução inesperada ou dump incompleto; nada foi executado.')
    return linhas


def ler_sqlite(caminho):
    uri = Path(caminho).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conexao:
        conexao.execute('PRAGMA trusted_schema=OFF')
        conexao.execute('PRAGMA query_only=ON')
        if conexao.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite: quick_check falhou.')
        tipo = conexao.execute("SELECT type FROM sqlite_schema WHERE name='questoes_oab'").fetchone()
        if tipo != ('table',):
            raise ValueError('SQLite: tabela questoes_oab ausente ou inválida.')
        colunas = [r[1] for r in conexao.execute('PRAGMA table_info(questoes_oab)')]
        if colunas != ['id', *CAMPOS]:
            raise ValueError('SQLite: esquema de colunas inesperado.')
        return [converter_bd(r) for r in conexao.execute('SELECT ' + ', '.join(CAMPOS) + ' FROM questoes_oab')]


def elegivel(q, preliminar=False):
    resposta = q['resposta_definitiva'] or (q['resposta_preliminar'] if preliminar else '')
    return q['numero_exame'] not in ANTIGOS and q['questao_extraida'] == 'True' and resposta in ('A','B','C','D')


def indexar(linhas, origem, erros):
    indice = {}
    for q in linhas:
        k = chave(q)
        if (not re.fullmatch(r'[1-9]\d*(?:\.\d+)?', k[0]) or k[1] != '1'
                or not re.fullmatch(r'[1-9]\d*', k[2])):
            erros.append(f'{origem}: identificação inválida {k!r}.')
            continue
        if k in indice:
            erros.append(f'{origem}: questão duplicada {rotulo(k)}.')
            continue
        indice[k] = q
        if q['questao_extraida'] not in ('True', 'False'):
            erros.append(f'{origem}: {rotulo(k)} flag de extração inválida.')
        if q['gabarito_definitivo'] not in ('true', 'false') or (q['gabarito_definitivo'] == 'true') != bool(q['resposta_definitiva']):
            erros.append(f'{origem}: {rotulo(k)} flag de gabarito definitivo inconsistente.')
        letras = {'', 'A', 'B', 'C', 'D', 'ANULADA'} | ({'E'} if k[0] in ANTIGOS else set())
        for campo in ('resposta_preliminar', 'resposta_definitiva'):
            if q[campo] not in letras:
                erros.append(f'{origem}: {rotulo(k)} {campo} inválido.')
        if not q['fonte_prova'].strip():
            erros.append(f'{origem}: {rotulo(k)} sem fonte da prova.')
        if elegivel(q, True) and any(not q[c].strip() for c in TEXTOS):
            erros.append(f'{origem}: {rotulo(k)} elegível com campo textual ausente.')
    if not indice:
        erros.append(f'{origem}: base sem registros válidos.')
    return indice


def comparar(csv_ind, outro, origem, erros):
    for k in sorted(csv_ind.keys() - outro.keys(), key=ordem):
        erros.append(f'{origem}: ausente {rotulo(k)}.')
    for k in sorted(outro.keys() - csv_ind.keys(), key=ordem):
        erros.append(f'{origem}: registro extra {rotulo(k)}.')
    for k in sorted(csv_ind.keys() & outro.keys(), key=ordem):
        diferentes = [c for c in CAMPOS if csv_ind[k][c] != outro[k][c]]
        if diferentes:
            erros.append(f'{origem}: {rotulo(k)} diverge do CSV em {", ".join(diferentes)}.')


def normalizar(t):
    t = unicodedata.normalize('NFD', t.lower())
    return ' '.join(''.join(c for c in t if not unicodedata.combining(c)).split())


def auditar(indice):
    achados = {k: set() for k in INDICADORES}
    for k, q in indice.items():
        textos = [normalizar(q[c]) for c in TEXTOS]
        alternativas = [x for x in textos[1:] if x]
        # Um marcador é resíduo provável, não comprovação de texto truncado.
        marcador = any(int(m) == int(k[2]) + 1 for t in textos
                       for m in re.findall(r'\bquestao\s*(\d{1,3})\b', t))
        proxima = indice.get((k[0], k[1], str(int(k[2]) + 1)))
        flags = {
            'total': True, 'definitivo': elegivel(q), 'preliminar': elegivel(q, True),
            'nao_extraida': q['questao_extraida'] != 'True',
            'sem_materia': not q['materia'].strip(), 'marcador': marcador,
            'cabecalho': any(re.search(r'exame de ordem unificado|caderno de prova\s*[-–]?\s*tipo|prova tipo\s*[1-4]', t) for t in textos),
            'd_longa': len(q['alternativa_d']) > 3 * max(1, *(len(q[c]) for c in TEXTOS[1:4])),
            'identicas': len(set(alternativas)) < len(alternativas),
            'questionario': any(re.search(r'questionario de percepcao|qual foi o tempo gasto|tempo gasto para concluir|layout.{0,100}(?:desta|dessa|da) prova', t) for t in textos),
            'vizinha_ausente': q['questao_extraida'] == 'True' and proxima is not None and proxima['questao_extraida'] == 'False',
        }
        for nome, presente in flags.items():
            if presente:
                achados[nome].add(k)
    return achados


def ler_anterior(ref):
    commit = subprocess.check_output(['git', '-C', str(RAIZ), 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}'], stderr=subprocess.PIPE).decode('ascii').strip()
    dados = subprocess.check_output(['git', '-C', str(RAIZ), 'show', commit + ':dados/questoes_oab.csv'], stderr=subprocess.PIPE)
    return ler_csv(dados.decode('utf-8-sig')), commit


def md(t):
    return str(t).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('\n',' ')


def relatorio(achados, erros, estados, anterior=None, ref=None, aviso=None):
    linhas = ['## Integridade e qualidade da base OAB', '',
              '**Erros estruturais bloqueiam o check; alertas textuais exigem revisão humana.**', '',
              '| Exportação | Situação |', '|---|---|']
    linhas.extend(f'| {md(k)} | {md(v)} |' for k,v in estados.items())
    linhas += ['', f'Erros estruturais: **{len(erros)}**.', '']
    if erros:
        linhas.extend('- ' + md(e) for e in erros[:40])
        if len(erros)>40:
            linhas.append(f'- Mais {len(erros)-40} erros não exibidos.')
    if achados is None:
        return '\n'.join(linhas)+'\n'
    if aviso:
        linhas.extend(['', md(aviso)])
    linhas += ['', '### Indicadores gerais', '']
    if anterior is not None:
        linhas += [f'Comparação com `{md(ref)}`.', '', '| Indicador | Antes | Atual | Variação |', '|---|---:|---:|---:|']
        for k,nome in INDICADORES.items():
            a,b=len(anterior[k]),len(achados[k]); linhas.append(f'| {nome} | {a} | {b} | {b-a:+d} |')
    else:
        linhas += ['| Indicador | Atual |','|---|---:|']
        linhas.extend(f'| {nome} | {len(achados[k])} |' for k,nome in INDICADORES.items())
    linhas += ['', '### Alertas por exame', '', '| Exame | Não extraídas | Sem matéria | Marcador | Cabeçalho | D longa | Idênticas | Questionário | Vizinha ausente |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    contagens={k:Counter(x[0] for x in achados[k]) for k in ALERTAS}
    for e in sorted({x[0] for x in achados['total']},key=lambda s:tuple(map(int,s.split('.')))):
        linhas.append('| '+e+' | '+' | '.join(str(contagens[k][e]) for k in ALERTAS)+' |')
    linhas += ['', '### Localização dos alertas', '', 'Até 20 exemplos por indicador; as categorias podem se sobrepor. A ausência de alertas não certifica o conteúdo.']
    for k in ALERTAS:
        if achados[k]:
            exemplos=', '.join(rotulo(x) for x in sorted(achados[k],key=ordem)[:20])
            linhas.append(f'- {INDICADORES[k]}: {exemplos}' + (' (lista parcial)' if len(achados[k])>20 else ''))
        if anterior is not None:
            novos=achados[k]-anterior[k]; removidos=anterior[k]-achados[k]
            if novos or removidos:
                linhas.append(f'  - Novos: {len(novos)}; removidos: {len(removidos)}.')
                if novos: linhas.append('  - Novos (até 20): '+', '.join(rotulo(x) for x in sorted(novos,key=ordem)[:20]))
                if removidos: linhas.append('  - Removidos (até 20): '+', '.join(rotulo(x) for x in sorted(removidos,key=ordem)[:20]))
    if anterior is not None:
        adicionadas=achados['total']-anterior['total']; retiradas=anterior['total']-achados['total']
        linhas += ['', f'Registros adicionados: {len(adicionadas)}; retirados: {len(retiradas)}.']
        for nome, chaves in (('Adicionados', adicionadas), ('Retirados', retiradas)):
            if chaves:
                exemplos = ', '.join(rotulo(x) for x in sorted(chaves, key=ordem)[:20])
                linhas.append(f'- {nome} (até 20): {exemplos}' +
                              (' (lista parcial)' if len(chaves) > 20 else ''))
    return '\n'.join(linhas)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pasta',type=Path,default=RAIZ/'dados')
    p.add_argument('--comparar-ref',help='Commit Git anterior (somente leitura).')
    p.add_argument('--resumo',type=Path,help='Acrescenta relatório Markdown ao arquivo.')
    a=p.parse_args(argv)
    erros, estados, indices = [], {}, {}
    leitores={'CSV':lambda:ler_csv((a.pasta/'questoes_oab.csv').read_text(encoding='utf-8-sig')),
              'SQL':lambda:ler_sql((a.pasta/'questoes_oab.sql').read_text(encoding='utf-8-sig')),
              'SQLite':lambda:ler_sqlite(a.pasta/'questoes_oab.sqlite')}
    for nome,ler in leitores.items():
        antes=len(erros)
        try:
            indices[nome]=indexar(ler(),nome,erros)
            if nome!='CSV' and 'CSV' in indices:
                comparar(indices['CSV'],indices[nome],nome,erros)
            estados[nome]='OK' if len(erros)==antes else 'ERRO'
        except (OSError,ValueError,csv.Error,sqlite3.Error) as e:
            erros.append(f'{nome}: {e}'); estados[nome]='ERRO'
    achados=auditar(indices['CSV']) if 'CSV' in indices else None
    anterior=ref=aviso=None
    if a.comparar_ref and not re.fullmatch(r'0+',a.comparar_ref):
        try:
            registros,ref=ler_anterior(a.comparar_ref)
            problemas=[]; indice=indexar(registros,'Anterior',problemas)
            if problemas:
                aviso='Comparação indisponível: base anterior tem erros estruturais.'
            else:
                anterior=auditar(indice)
        except (OSError,ValueError,csv.Error,subprocess.CalledProcessError):
            aviso='Comparação indisponível: não foi possível ler a base anterior no Git.'
    texto=relatorio(achados,erros,estados,anterior,ref,aviso)
    print(texto,end='')
    if a.resumo:
        with a.resumo.open('a',encoding='utf-8') as f:
            f.write('\n'+texto)
    return int(bool(erros))


if __name__=='__main__':
    sys.exit(main())

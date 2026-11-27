"""Relatório operacional de cobertura e sobreposições, sem executar LLMs."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

import avaliar_oab as A
import validar_resultados as V


def ordem(chave):
    return tuple(int(x) for x in chave.split('.'))


def identidade(config):
    # Prova, limite e caminho dos dados definem a seleção, não o protocolo.
    return tuple(config[k] for k in A.CONFIG_QUE_TRAVA)


def descobrir(pasta):
    pares = set()
    for p in pasta.rglob('*'):
        if not p.is_file() or p.suffix not in ('.csv', '.json'):
            continue
        if p.relative_to(pasta).parts[0] == 'historico':
            continue
        pares.add(p.with_suffix('.csv'))
    return sorted(pares)


def consolidar(pasta, base):
    pasta, base = Path(pasta), Path(base)
    grupos, excluidos = {}, []
    for caminho in descobrir(pasta):
        nome = caminho.relative_to(pasta).as_posix()
        try:
            # Rodadas interrompidas podem contribuir para a cobertura, sem
            # alterar a política estrita do validador usado para novos PRs.
            V.validar(caminho, base, permitir_parcial=True)
            config = V.ler_config(caminho.with_suffix('.json'))
            id_grupo = identidade(config)
            with caminho.open(encoding='utf-8-sig', newline='') as f:
                linhas = list(csv.DictReader(f, strict=True))
            if id_grupo not in grupos:
                bruto, origem_base = V.resolver_base(base, config['sha256_dados'])
                questoes = A.carregar_questoes(bruto, config['gabarito'])
                elegiveis = {V.chave(q) for q in questoes}
                if len(elegiveis) != len(questoes):
                    raise ValueError('Questões duplicadas na base elegível.')
                grupos[id_grupo] = {'config': config, 'elegiveis': elegiveis,
                                    'ocorrencias': defaultdict(dict), 'arquivos': [],
                                    'origem_base': origem_base}
            grupo = grupos[id_grupo]
            grupo['arquivos'].append(nome)
            for linha in linhas:
                grupo['ocorrencias'][V.chave(linha)][nome] = linha['acertou']
        except (OSError, ValueError, KeyError, TypeError, OverflowError, csv.Error) as erro:
            excluidos.append((nome, str(erro)))
    return list(grupos.values()), excluidos


def indicadores(grupo):
    elegiveis = grupo['elegiveis']
    ocorrencias = grupo['ocorrencias']
    cobertas = set(ocorrencias)
    exames = defaultdict(set)
    for chave in elegiveis:
        exames[chave[0]].add(chave)
    por_exame = [(n, len(exames[n] & cobertas), len(exames[n]))
                 for n in sorted(exames, key=ordem, reverse=True)]
    repetidas = {k: v for k, v in ocorrencias.items() if len(v) > 1}
    divergentes = sum(len(set(v.values())) > 1 for v in repetidas.values())
    # Não escolher arbitrariamente uma rodada para calcular acurácia.
    acertos = None if repetidas else sum(int(next(iter(v.values())))
                                         for v in ocorrencias.values())
    return {'cobertas': len(cobertas), 'total': len(elegiveis),
            'por_exame': por_exame, 'repetidas': repetidas,
            'divergentes': divergentes, 'acertos': acertos,
            'concluidos': sum(n == total for _, n, total in por_exame)}


def gerar_relatorio(grupos, excluidos):
    md = V.texto_md
    linhas = ['## Cobertura e integridade dos resultados', '',
              'Cobertura por questão única e configuração experimental. '
              'Resultados históricos não são considerados. Sobreposições e arquivos '
              'incompatíveis são alertas; não alteram o check de validação.', '']
    if not grupos:
        linhas.extend(['Nenhum resultado compatível para consolidar.', ''])
    for grupo in grupos:
        c = grupo['config']
        i = indicadores(grupo)
        n, total = i['cobertas'], i['total']
        repetidas = i['repetidas']
        acuracia = '— (sobreposição)' if repetidas else f"{i['acertos']/n:.1%}"
        linhas.extend([
            f"### {md(c['modelo'])} — {md(c['onde'])} / {md(c['gabarito'])}", '',
            f"Temperatura: {md(c['temperatura'])} | Tokens: {md(c['max_tokens'])} | Semente: {md(c['semente'])}",
            f"Base SHA-256: `{c['sha256_dados']}`  ",
            f"Origem da base: {md(grupo['origem_base'])}  ",
            f"Prompt SHA-256: `{c['sha256_prompt']}`  ",
            f"Avaliador SHA-256: `{c['sha256_script']}`", '',
            f"**Cobertura: {n}/{total} ({n/total:.1%})** | Exames concluídos: {i['concluidos']}/{len(i['por_exame'])} | Arquivos: {len(grupo['arquivos'])}",
            f"Questões pendentes: {total-n} | Questões sobrepostas: {len(repetidas)} | Acurácia observada: {acuracia}", '',
            '| Exame | Respondidas | Elegíveis | Status |', '|---|---:|---:|---|',
        ])
        for exame, feitas, esperadas in i['por_exame']:
            status = 'Concluído' if feitas == esperadas else 'Parcial' if feitas else 'Pendente'
            linhas.append(f'| {md(exame)} | {feitas} | {esperadas} | {status} |')
        if repetidas:
            linhas.extend(['', '**Alerta: questões presentes em mais de um arquivo.**',
                           f"Questões com acertos divergentes: {i['divergentes']}. A cobertura conta cada questão apenas uma vez; a acurácia agregada é omitida.", ''])
            conjuntos = defaultdict(list)
            for chave, arquivos in repetidas.items():
                conjuntos[tuple(sorted(arquivos))].append(chave)
            linhas.extend(['| Arquivos envolvidos | Questões | Exemplos (exame/questão) |', '|---|---:|---|'])
            for arquivos, chaves in sorted(conjuntos.items())[:30]:
                exemplos = ', '.join('/'.join(k) for k in sorted(chaves)[:5])
                linhas.append(f"| {', '.join(md(a) for a in arquivos)} | {len(chaves)} | {md(exemplos)} |")
            if len(conjuntos) > 30:
                linhas.append(f'\nMais {len(conjuntos)-30} conjuntos de arquivos sobrepostos (não exibidos).')
        linhas.append('')
    if excluidos:
        linhas.extend(['### Arquivos não consolidados', '',
                       'Verifique a configuração, a disponibilidade da base no Git, a versão local do avaliador ou as inconsistências informadas.', '',
                       '| Arquivo | Motivo |', '|---|---|'])
        for arquivo, erro in excluidos:
            linhas.append(f'| {md(arquivo)} | {md(erro)} |')
        linhas.append('')
    return '\n'.join(linhas) + '\n'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resultados', type=Path, default=V.RAIZ / 'resultados')
    p.add_argument('--base', type=Path, default=V.RAIZ / 'dados/questoes_oab.csv')
    p.add_argument('--resumo', type=Path, help='Acrescenta o relatório ao arquivo Markdown.')
    a = p.parse_args(argv)
    grupos, excluidos = consolidar(a.resultados, a.base)
    relatorio = gerar_relatorio(grupos, excluidos)
    if a.resumo is not None:
        with a.resumo.open('a', encoding='utf-8', newline='\n') as f:
            f.write('\n' + relatorio)
    else:
        print(relatorio, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

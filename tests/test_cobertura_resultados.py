"""Testes do relatório consolidado com resultados sintéticos."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

import avaliar_oab as A
import validar_resultados as V
import cobertura_resultados as C


class CoberturaResultados(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.pasta = self.raiz / 'resultados'
        self.pasta.mkdir()
        self.base = self.raiz / 'base.csv'
        campos = ['numero_exame', 'numero_questao', 'materia', 'enunciado',
                  'alternativa_a', 'alternativa_b', 'alternativa_c', 'alternativa_d',
                  'resposta_preliminar', 'resposta_definitiva', 'questao_extraida']
        with self.base.open('w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(campos)
            for e, q, g in [('46', '1', 'A'), ('47', '1', 'A'), ('47', '2', 'B'), ('47', '3', 'ANULADA')]:
                w.writerow([e, q, 'Civil', 'Enunciado', 'a', 'b', 'c', 'd', 'A', g, True])
        self.config = {'modelo': 'simulado', 'onde': 'ollama', 'temperatura': 0.0,
                       'max_tokens': 1024, 'gabarito': 'definitivo', 'prova': None, 'limite': None,
                       'dados': str(self.base), 'semente': A.SEMENTE,
                       'sha256_dados': V.sha256(self.base.read_bytes()),
                       'sha256_prompt': V.sha256(A.PROMPT.encode()),
                       'sha256_script': V.sha256(Path(A.__file__).read_bytes())}

    def gravar(self, nome, chaves, *, letra='A', **config):
        p = self.pasta / (nome + '.csv')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.with_suffix('.json').write_text(json.dumps(self.config | config), encoding='utf-8')
        with p.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, A.CAMPOS)
            w.writeheader()
            for e, q in chaves:
                g = 'B' if q == '2' else 'A'
                w.writerow(dict(zip(A.CAMPOS, [e, q, 'Civil', g, 'definitivo', letra, int(letra == g), 1, letra])))
        return p

    def relatorio(self):
        return C.consolidar(self.pasta, self.base)

    def test_uniao_de_provas_sem_sobreposicao(self):
        self.gravar('p46', [('46', '1')], prova=['46'])
        self.gravar('p47', [('47', '1'), ('47', '2')], prova=['47'])
        grupos, excluidos = self.relatorio()
        self.assertEqual(len(grupos), 1)
        self.assertFalse(excluidos)
        i = C.indicadores(grupos[0])
        self.assertEqual((i['cobertas'], i['total'], i['concluidos'], i['acertos']), (3, 3, 2, 2))
        self.assertIn('3/3 (100.0%)', C.gerar_relatorio(grupos, excluidos))

    def test_parcial_e_pendente(self):
        self.gravar('p47', [('47', '1')], prova=['47'])
        grupos, excluidos = self.relatorio()
        self.assertFalse(excluidos)
        i = C.indicadores(grupos[0])
        self.assertEqual(i['por_exame'], [('47', 1, 2), ('46', 0, 1)])
        self.assertEqual(i['concluidos'], 0)
        r = C.gerar_relatorio(grupos, excluidos)
        self.assertIn('| 47 | 1 | 2 | Parcial |', r)
        self.assertIn('| 46 | 0 | 1 | Pendente |', r)

    def test_sobreposicao_nao_inflaciona_cobertura(self):
        self.gravar('a', [('47', '1')], prova=['47'])
        self.gravar('b', [('47', '1'), ('47', '2')], prova=['47'])
        grupos, excluidos = self.relatorio()
        i = C.indicadores(grupos[0])
        self.assertEqual(i['cobertas'], 2)
        self.assertEqual(len(i['repetidas']), 1)
        self.assertIsNone(i['acertos'])
        self.assertIn('Questões sobrepostas: 1', C.gerar_relatorio(grupos, excluidos))
        self.assertIn('a.csv, b.csv', C.gerar_relatorio(grupos, excluidos))

    def test_sobreposicao_com_acerto_divergente(self):
        self.gravar('a', [('47', '1')])
        self.gravar('b', [('47', '1')], letra='B')
        grupos, _ = self.relatorio()
        self.assertEqual(C.indicadores(grupos[0])['divergentes'], 1)

    def test_configuracoes_distintas_nao_se_misturam(self):
        self.gravar('a', [('47', '1')])
        self.gravar('b', [('47', '1')], temperatura=0.7)
        self.gravar('c', [('47', '1')], modelo='outro')
        self.gravar('d', [('47', '1')], onde='openrouter')
        self.gravar('e', [('47', '1')], gabarito='preliminar')
        grupos, excluidos = self.relatorio()
        self.assertFalse(excluidos)
        self.assertEqual(len(grupos), 5)
        self.assertTrue(all(not C.indicadores(g)['repetidas'] for g in grupos))

    def test_hash_incompativel_excluido(self):
        self.gravar('antigo', [('47', '1')], sha256_dados='0'*64)
        grupos, excluidos = self.relatorio()
        self.assertFalse(grupos)
        self.assertIn('Hash incompatível', excluidos[0][1])

    def test_historico_ignorado(self):
        self.gravar('historico/antigo', [('47', '1')])
        self.assertEqual(self.relatorio(), ([], []))

    def test_par_incompleto_e_resultado_invalido(self):
        p = self.gravar('sem_json', [('47', '1')])
        p.with_suffix('.json').unlink()
        p = self.gravar('sem_csv', [('47', '1')])
        p.unlink()
        self.gravar('anulada', [('47', '3')])
        grupos, excluidos = self.relatorio()
        self.assertFalse(grupos)
        self.assertEqual(len(excluidos), 3)
        self.assertIn('Arquivos não consolidados', C.gerar_relatorio(grupos, excluidos))

    def test_resumo_vazio_e_append(self):
        destino = self.raiz / 'resumo.md'
        destino.write_text('Relatório anterior\n', encoding='utf-8')
        codigo = C.main(['--base', str(self.base), '--resultados', str(self.pasta), '--resumo', str(destino)])
        self.assertEqual(codigo, 0)
        texto = destino.read_text(encoding='utf-8')
        self.assertIn('Relatório anterior', texto)
        self.assertIn('Nenhum resultado compatível', texto)

    def test_duplicatas_nao_bloqueiam_relatorio(self):
        self.gravar('a', [('47', '1')])
        self.gravar('b', [('47', '1')])
        destino = self.raiz / 'resumo.md'
        self.assertEqual(C.main(['--base', str(self.base), '--resultados', str(self.pasta), '--resumo', str(destino)]), 0)
        self.assertIn('Alerta', destino.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

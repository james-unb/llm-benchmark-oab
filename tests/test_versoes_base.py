"""Testes da recuperação de bases em um repositório Git temporário."""
import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import avaliar_oab as A
import cobertura_resultados as C
import validar_resultados as V


class VersoesBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.base = self.raiz / V.CAMINHO_BASE
        self.base.parent.mkdir()
        self.pasta = self.raiz / 'resultados'
        self.pasta.mkdir()
        mock = patch.object(V, 'RAIZ', self.raiz)
        mock.start()
        self.addCleanup(mock.stop)
        self.addCleanup(V.buscar_base_historica.cache_clear)
        self.git('init', '-q')
        self.git('config', 'core.autocrlf', 'false')
        self.gravar_base('A', 1)
        self.antiga = self.base.read_bytes()
        self.commit('base antiga')
        self.commit_antigo = self.git('rev-parse', 'HEAD').decode().strip()
        self.gravar_base('B', 2)
        self.atual = self.base.read_bytes()
        self.commit('base atual')

    def git(self, *args):
        return subprocess.check_output([
            'git', '-C', str(self.raiz), '-c', 'user.name=Teste',
            '-c', 'user.email=teste@example.invalid', '-c', 'commit.gpgsign=false', *args])

    def commit(self, mensagem):
        self.git('add', V.CAMINHO_BASE)
        self.git('commit', '-qm', mensagem)

    def gravar_base(self, gabarito, quantidade):
        campos = ['numero_exame', 'numero_questao', 'materia', 'enunciado',
                  'alternativa_a', 'alternativa_b', 'alternativa_c', 'alternativa_d',
                  'resposta_preliminar', 'resposta_definitiva', 'questao_extraida']
        with self.base.open('w', encoding='utf-8', newline='') as f:
            w = csv.writer(f, lineterminator='\n')
            w.writerow(campos)
            for n in range(1, quantidade+1):
                w.writerow(['47', n, 'Civil', 'Enunciado', 'a', 'b', 'c', 'd', gabarito, gabarito, True])

    def resultado(self, nome, base, gabarito, quantidade):
        p = self.pasta / (nome + '.csv')
        c = {'modelo': 'simulado', 'onde': 'ollama', 'temperatura': 0.0,
             'max_tokens': 1024, 'gabarito': 'definitivo', 'prova': ['47'],
             'limite': None, 'dados': str(self.base), 'semente': A.SEMENTE,
             'sha256_dados': V.sha256(base), 'sha256_script': V.sha256(Path(A.__file__).read_bytes()),
             'sha256_prompt': V.sha256(A.PROMPT.encode())}
        p.with_suffix('.json').write_text(json.dumps(c), encoding='utf-8')
        with p.open('w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(A.CAMPOS)
            for n in range(1, quantidade+1):
                w.writerow(['47', n, 'Civil', gabarito, 'definitivo', 'A', int(gabarito == 'A'), 1, 'A'])
        return p

    def test_base_atual_preferida_sem_git(self):
        with patch.object(V, 'git_local', side_effect=AssertionError('Git desnecessário')):
            self.assertEqual(V.resolver_base(self.base, V.sha256(self.atual)), (self.atual, 'Local'))

    def test_rodada_antiga_usa_gabarito_antigo(self):
        p = self.resultado('antiga', self.antiga, 'A', 1)
        r = V.validar(p, self.base)
        self.assertEqual((r['acertos'], r['esperadas']), (1, 1))
        self.assertEqual(r['origem_base'], 'Git: ' + self.commit_antigo)
        self.assertIn(self.commit_antigo, V.gerar_resumo([{'arquivo': str(p), 'resultado': r}]))
        self.assertEqual(self.base.read_bytes(), self.atual)

    def test_hash_antigo_crlf(self):
        crlf = self.antiga.replace(b'\n', b'\r\n')
        bruto, origem = V.resolver_base(self.base, V.sha256(crlf))
        self.assertEqual(bruto, crlf)
        self.assertEqual(origem, 'Git: ' + self.commit_antigo)

    def test_hash_desconhecido_rejeitado(self):
        with self.assertRaisesRegex(ValueError, 'não encontrado'):
            V.resolver_base(self.base, '0'*64)

    def test_base_externa_nao_busca_no_git(self):
        externa = self.raiz / 'externa.csv'
        externa.write_bytes(self.atual)
        with patch.object(V, 'git_local', side_effect=AssertionError('Git desnecessário')):
            with self.assertRaisesRegex(ValueError, 'Hash incompatível'):
                V.resolver_base(externa, V.sha256(self.antiga))

    def test_cobertura_separa_versoes_da_base(self):
        self.resultado('antiga', self.antiga, 'A', 1)
        self.resultado('atual', self.atual, 'B', 2)
        grupos, excluidos = C.consolidar(self.pasta, self.base)
        self.assertFalse(excluidos)
        self.assertEqual(len(grupos), 2)
        self.assertEqual(sorted((C.indicadores(g)['cobertas'], C.indicadores(g)['total']) for g in grupos), [(1, 1), (2, 2)])
        self.assertTrue(all(not C.indicadores(g)['repetidas'] for g in grupos))
        self.assertIn(self.commit_antigo, C.gerar_relatorio(grupos, excluidos))

    def test_avaliador_incompativel_continua_rejeitado(self):
        p = self.resultado('antiga', self.antiga, 'A', 1)
        j = p.with_suffix('.json')
        c = json.loads(j.read_text(encoding='utf-8'))
        c['sha256_script'] = '0'*64
        j.write_text(json.dumps(c), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Hash incompatível'):
            V.validar(p, self.base)


if __name__ == '__main__':
    unittest.main()

# Questões da 1ª fase da OAB

Projeto do time de dados para coletar provas objetivas tipo 1, estruturar uma linha por questão e entregar os dados ao time de LLM.

## Escopo e fonte

O acervo contém **46 exames**: 2010.2, 2010.3 e os exames IV a XLVII. A base inclui os exames XXXIX, XLIV e XLVII com as respostas extraídas das seções oficiais Tipo 1. O VI Exame também está incluído, com o gabarito nacional separado do material da reaplicação de Duque de Caxias. A coleta considera somente o caderno tipo 1 da 1ª fase, gabaritos e comunicados oficiais; não inclui cadernos tipo 2–4 nem provas da 2ª fase.

Os PDFs estão em `provas_oab/`, organizados por `numero_exame_ano_Ttrimestre` (por exemplo, `16_2015_T1`). Os identificadores oficiais antigos são mantidos: `2010.2_2010_T3` e `2010.3_2011_T1`. Os trimestres são os trimestres civis da aplicação. Os arquivos preservam o identificador da pasta no nome.

A fonte é a [página oficial de Editais e Provas da OAB/FGV](https://examedeordem.oab.org.br/EditaisProvas?NumeroExame=0). O downloader trata links PDF antigos publicados em HTTP e acessa-os por HTTPS.

O parser também atende aos formatos históricos: 2010.2 e 2010.3 têm 100 questões; os outros exames deste acervo têm 80. Questionários de percepção anexados após o caderno não são questões. As questões 80 dos exames XV e XVI são reconstruídas a partir da leitura visual dos PDFs oficiais porque a ordem interna do texto nesses arquivos intercala colunas. Nos gabaritos dos exames XII a XIV, o parser lê a tabela que relaciona os tipos de caderno e extrai as respostas do tipo 1. A matéria permanece vazia antes do XVI porque a distribuição histórica ainda não foi validada.

O [plano de extração retificado](plano_extracao_feito.pdf) registra a estrutura e as decisões aplicadas.

## Preparar e executar

Instale o [uv](https://docs.astral.sh/uv/) e execute os comandos na raiz do repositório. É necessário instalar o Chromium do Playwright para baixar os documentos:

```powershell
uv sync
uv run playwright install chromium
uv run extensao tudo --exame-inicial 2010.2
```

O comando baixa documentos que ainda faltam e extrai o acervo local. O corte padrão do downloader é o XVI Exame; use `--exame-inicial 2010.2` para percorrer também os exames antigos. Para executar uma etapa isolada:

```powershell
uv run extensao baixar --exame-inicial 2010.2
uv run extensao extrair
```

Os caminhos são relativos à raiz do repositório: PDFs em `provas_oab/` e resultados em `dados/`. O OCR local é acionado quando a codificação do texto do PDF está corrompida; o RapidOCR pode baixar os modelos necessários na primeira execução.

## Dados entregues

Os artefatos gerados estão versionados em `dados/`:

- `questoes_oab.sqlite`: banco SQLite pronto para consulta;
- `questoes_oab.sql`: schema, registros e view, compatíveis com SQLite;
- `questoes_oab.csv`: CSV UTF-8 com BOM.

Cada linha representa uma questão e inclui número e data do exame, tipo da prova (sempre 1), número da questão, matéria, enunciado, alternativas A–D, respostas preliminar e definitiva, `questao_extraida`, `gabarito_definitivo` e caminhos relativos dos PDFs fonte. Alternativa E não é armazenada. Valores sem fonte ou conteúdo permanecem vazios no CSV e `NULL` no SQLite/SQL.

`resposta_definitiva` contém a resposta final ou `ANULADA`. `gabarito_definitivo` é `true` quando uma fonte oficial confirma a resposta definitiva e `false` quando não há gabarito definitivo separado confirmado. Em edições em que todos os recursos foram indeferidos, a banca manteve o preliminar e não publicou um arquivo definitivo separado: nesses casos, a resposta preliminar continua preenchida, enquanto `resposta_definitiva` e `fonte_gabarito_definitivo` ficam vazias e `gabarito_definitivo` fica `false`. Esse vazio é esperado e não indica falha de coleta de um arquivo existente. Quando comunicado oficial confirma que o preliminar foi mantido integralmente, ele também é registrado como resposta definitiva.

`questao_extraida=false` marca registros com enunciado ou alternativas A–D incompletos. Os registros ficam na tabela principal para consulta e revisão. A view `questoes_llm` filtra questões anuladas, incompletas ou sem resposta definitiva confirmada.

## Uso pelo time de LLM e estado do acervo

Na versão atual, a tabela principal tem **3.720 questões** em **46 exames**; a view `questoes_llm` contém **1.819 questões**. Dez registros têm `questao_extraida=false` e ficam fora da view. A base mantém 2010.2 e 2010.3 (100 questões cada) e o VI Exame (80 questões), além das demais provas tipo 1 do acervo. A matéria não foi preenchida nos exames anteriores ao XVI porque falta validar a classificação histórica por faixa.

Consulta inicial:

```sql
SELECT numero_exame, data_exame, numero_questao, materia, enunciado,
       alternativa_a, alternativa_b, alternativa_c, alternativa_d,
       resposta_preliminar, resposta_definitiva
FROM questoes_llm;
```

## Avaliar um modelo (time de LLM)

O script `avaliar_oab.py` aplica as questões a um modelo e grava um CSV com a resposta, o acerto e o tempo de cada questão. Ele declara as próprias dependências no cabeçalho, então roda com `uv` sem instalar o projeto. Para modelos locais é preciso ter o [Ollama](https://ollama.com) aberto e o modelo baixado (`ollama pull <modelo>`); para a nuvem, a variável `OPENROUTER_API_KEY`.

```powershell
uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --limite 20              # teste rápido
uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M                          # prova inteira
uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --prova 46 47            # só os exames XLVI e XLVII
uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --gabarito preliminar    # inclui questões só com preliminar
uv run avaliar_oab.py --modelo google/gemma-4-26b-a4b-it --onde openrouter
```

**Quais questões entram.** Questões com `questao_extraida=true` e alternativas A a D; 2010.2 e 2010.3 ficam de fora porque tinham alternativa E, e questões anuladas também. O gabarito depende de `--gabarito`:

- `definitivo` (padrão): só questões com gabarito definitivo publicado (1.819 questões na base atual).
- `preliminar`: usa o preliminar onde não há definitivo (3.485 questões, das quais 1.666 com preliminar). A base não registra se a banca manteve o preliminar, então cada linha do resultado traz `fonte_gabarito` (`definitivo` ou `preliminar`) e o resumo mostra quantas usaram o preliminar.

**Dividir a prova.** Com `--prova`, cada pessoa roda só os exames combinados; cada divisão vai para um arquivo próprio. Exame pedido que não existe na base dá erro com a lista dos disponíveis.

**Onde fica o resultado.** Em `resultados/resultados_<modelo>[_prova...][_gabarito-preliminar].csv` (ou no caminho de `--saida`), com a resposta completa do modelo em `texto_bruto`, sem corte. Ao lado fica um `.json` com a configuração da rodada: modelo, onde rodou, temperatura, máximo de tokens, regra do gabarito, exames, limite, de onde vieram os dados e o sha256 da base, do prompt e do script.

**Retomar.** Se parar no meio, rode o mesmo comando: ele continua de onde parou. Se o modelo, o serviço, a temperatura, o máximo de tokens, a regra do gabarito, a base (sha256), o prompt ou a versão do script (sha256) mudaram, o script recusa a retomada e pede outro `--saida`, para não misturar respostas de configurações diferentes. Para fixar uma versão da base, passe `--dados` com o caminho de um arquivo local ou a URL de um commit.

**Resumo.** Considera só as questões da seleção atual. Com `--limite` menor que a seleção, avisa que a rodada é parcial e que a acurácia vale só para as respondidas. Temperatura 0 e ordem embaralhada com semente 42, então `--limite N` dá a mesma amostra para todo mundo.

**Testes.** `python3 -m unittest discover -s tests` (não chama modelo nenhum).

**Histórico.** `resultados/historico/gemma4_12b-nvfp4_2026-10-28.csv` é a rodada do gemma4:12b-nvfp4 feita com a versão anterior do script: gabarito preliminar onde faltava o definitivo, `texto_bruto` cortado em 500 caracteres e a base com os problemas de gabarito e de extração apontados na revisão do PR #2. Fica como registro; não é a pontuação validada.


## Validação dos resultados

O script `validar_resultados.py` verifica a consistência e a rastreabilidade dos resultados das avaliações, sem executar modelos ou consumir créditos de APIs.

### Validação automática

O GitHub Actions executa automaticamente a validação dos arquivos de resultados adicionados ou modificados em um Pull Request.

Caso sejam identificadas inconsistências, o check será reprovado, impedindo o merge pelo fluxo normal até que os problemas sejam corrigidos.

As principais verificações realizadas são:

- Estrutura e correspondência dos arquivos CSV e JSON;
- Duplicidade e elegibilidade das questões;
- Consistência dos gabaritos e suas respectivas fontes;
- Exclusão de questões anuladas;
- Consistência das respostas e dos acertos;
- Cobertura das questões selecionadas;
- Hashes da base, do prompt e do avaliador;
- Validade dos tempos de resposta e cálculo da acurácia.

Os resultados armazenados em `resultados/historico/` não são submetidos à validação automática.

### Validação local (opcional)

O pesquisador pode verificar seus resultados antes de abrir um Pull Request, executando na raiz do repositório:

```bash
uv run validar_resultados.py resultados/resultados_gemma4_12b-nvfp4_prova47.csv
```

Para validar múltiplos arquivos, basta informar seus caminhos no mesmo comando.

A validação exige as versões correspondentes da base e do avaliador utilizados na rodada.

### Testes unitários

Os testes do avaliador e do validador podem ser executados localmente:

```bash
uv run python -m unittest discover -s tests -v
```

Os testes também são executados automaticamente pelo GitHub Actions, sem necessidade de intervenção manual dos pesquisadores.

### Relatório de validação

O resumo da execução (Summary) do GitHub Actions apresenta uma linha por arquivo de resultados modificado, com modelo, provas, regra de gabarito, cobertura da seleção, acertos, acurácia, respostas sem letra válida, tempo médio e situação da validação. Em caso de erro, as inconsistências são apresentadas no resumo e nos logs; o check continua reprovado.

Opcionalmente, o pesquisador pode gerar o mesmo relatório em Markdown localmente:

```bash
uv run validar_resultados.py resultados/resultados_gemma4_12b-nvfp4_prova47.csv --resumo resumo.md
```

O relatório não consolida rodadas de modelos ou configurações diferentes. A opção `--resumo` acrescenta o relatório ao arquivo informado; use um novo caminho para cada execução, se desejar.

### Cobertura e sobreposições dos resultados

O Summary do GitHub Actions também apresenta um relatório operacional do acervo de resultados, com questões únicas respondidas, questões pendentes, exames concluídos, parciais e pendentes, e acurácia observada quando não há sobreposições.

As rodadas são separadas por modelo, serviço, temperatura, limite de tokens, regra de gabarito, semente e hashes registrados da base, do prompt e do avaliador. Seleções de provas e limites de questões diferentes podem contribuir para a mesma configuração. A cobertura usa todas as questões elegíveis daquela configuração como denominador, e não apenas as provas selecionadas em um arquivo.

Questões presentes em mais de um arquivo da mesma configuração são contabilizadas uma única vez na cobertura. O relatório identifica os arquivos envolvidos e omite a acurácia agregada quando há sobreposição, sem bloquear automaticamente o PR. Essa sinalização não prova que uma repetição experimental seja indevida. Configurações com hashes distintos são mantidas separadas, inclusive quando a diferença se deve às quebras de linha.

Resultados com a base local ou uma versão anterior recuperada do histórico Git e com o avaliador local compatível são consolidados. Cada versão da base permanece em um grupo separado. Arquivos incompatíveis ou inconsistentes são listados como alertas e os históricos são ignorados. A validação obrigatória de resultados modificados permanece inalterada. O relatório também considera rodadas parciais válidas na cobertura, sem relaxar a validação obrigatória para novos resultados.

Para consultar a cobertura localmente (opcional):

```bash
uv run cobertura_resultados.py
```

Para gravar o relatório, use `--resumo cobertura.md`. O comando não executa modelos nem modifica os resultados.

### Validação de versões anteriores da base

Se a base padrão (`dados/questoes_oab.csv`) não corresponder ao SHA-256 registrado no JSON, o validador busca a versão exata no histórico Git local, entre os commits alcançáveis pelo HEAD. A cobertura utiliza a mesma versão recuperada e informa sua origem. A comparação continua exigindo o hash registrado (incluindo a compatibilidade LF/CRLF); não são usados gabaritos da versão atual para corrigir rodadas antigas.

O GitHub Actions já baixa o histórico completo (`fetch-depth: 0`). Localmente, se necessário, atualize a revisão do repositório e seu histórico; em clones rasos, pode ser necessário `git fetch --unshallow`. Uma base externa informada por `--base` precisa corresponder diretamente ao hash registrado. O validador não baixa URLs nem executa código de commits anteriores.

O suporte é restrito a versões anteriores da **base**: os hashes do avaliador e do prompt ainda precisam corresponder às versões locais. Se a base não puder ser localizada, a validação de um novo resultado falha e o relatório de cobertura indica a incompatibilidade.

Para acompanhar o progresso sem executar comandos, consulte [Testes automatizados no GitHub Actions](https://github.com/james-unb/llm-benchmark-oab/actions/workflows/tests.yml), abra a execução mais recente da branch `main` e veja a seção de cobertura no Summary.

### Integridade e qualidade da base OAB

O check `testes` também executa `validar_base.py` e publica o relatório no Summary. O script usa somente a biblioteca padrão do Python e não executa modelos nem altera os arquivos de dados.

Erros estruturais (chaves duplicadas ou inválidas, gabaritos/flags inválidos, campos textuais ausentes em questões elegíveis, exportações ausentes ou divergentes) reprovam o check. Os 17 campos exportados são comparados por chave de questão entre CSV, SQL e SQLite; IDs internos não são usados na comparação. Campos vazios do CSV equivalem a NULL no banco e quebras de linha são normalizadas. O SQLite é aberto apenas para leitura e submetido a `quick_check`. O dump SQL é interpretado como dados no formato atual de exportação, sem executar instruções; uma mudança intencional de esquema ou formato deve atualizar o validador e seus testes.

Alertas não bloqueantes incluem questões não extraídas, ausência de matéria, marcadores da questão seguinte, possíveis cabeçalhos/questionários, alternativas idênticas ou D com mais de três vezes o comprimento da maior entre A, B e C. Também são sinalizadas questões extraídas imediatamente anteriores a uma questão não extraída, como indício de possível erro de segmentação. Esses critérios são heurísticos: não comprovam truncamento nem correspondência semântica e não substituem a conferência com os PDFs originais. O relatório distingue os dois modos de elegibilidade usados pelo avaliador.

Nos PRs, a comparação usa o commit-base informado pelo GitHub; em pushes à `main`, usa o commit anterior ao push. A versão anterior é lida do histórico Git local, sem restaurar arquivos. O relatório mostra as variações dos indicadores e exemplos de alertas introduzidos e removidos. Em execução manual, ou quando a referência anterior está indisponível, é exibida apenas a situação atual (com aviso quando a comparação solicitada não pode ser feita).

Para executar localmente:

```bash
python validar_base.py
python validar_base.py --comparar-ref main --resumo qualidade_base.md
```

Use `--pasta` para auditar outra pasta com os três arquivos `questoes_oab.csv`, `questoes_oab.sql` e `questoes_oab.sqlite`. O argumento `--resumo` acrescenta o relatório ao arquivo indicado.

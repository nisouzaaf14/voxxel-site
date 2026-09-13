# Voxxel — rodada local de projetos

Status: implementação local testada; revisão visual no navegador pendente. Sem push ou deploy.

## Escopo

- Nova entrada /projetos, com composição CAD vetorial original e três caminhos.
- /orcamento preserva a implementação avançada: seis etapas, categorias e briefings dinâmicos, uploads, dimensões, materiais, perfis, cálculo, decomposição da estimativa e canvas volumétrico. Campos técnicos continuam expostos.
- /simplificado reutiliza linguagem visual e uploads do sistema principal. Solicita referência, descrição, quantidade, medidas opcionais e contato; sem seleção técnica obrigatória.
- /empresas usa o mesmo formulário-base guiado e acabamento do sistema principal, adicionando empresa, CNPJ opcional, prazo desejado, recorrência e quantidade aproximada.
- Envio anônimo e acompanhamento existente mantidos. Dados empresariais extras são registrados no briefing; quantidade aproximada B2B fica descritiva, não altera cálculo financeiro.
- Catálogo: ajuste aditivo no alinhamento de cards, imagem contida, títulos e indicação discreta de ilustração. Página de produto recebe apenas ajuste de enquadramento. Checkout não foi reconstruído.

## Arquivos

Criados: templates/projetos_entrada.html, templates/projeto_guiado.html, templates/components/project_modes.html, templates/components/project_uploads.html, static/css/project-system-v16.css, static/js/project-guided.js, tests/test_project_modes.py.

Modificados: app.py, templates/orcamento.html, templates/base.html.

A restauração preparada no início desta execução também acrescentou static/css/quote-restore-v16.css.

## Imagens

Nenhuma nova imagem de produto gerada nesta rodada: os 18 produtos locais já têm ilustrações da rodada anterior e todas as 18 rotas de imagem retornaram WebP corretamente nos testes. Nenhum produto local ficou sem arquivo de imagem. A revisão estética/fidelidade integral das 18 ilustrações ainda não foi concluída; não confundir cobertura técnica com aprovação visual. Nenhum dado do catálogo em produção foi consultado ou alterado nesta execução.

## Testes

13 testes automatizados aprovados: núcleo existente, renderização das rotas, preservação de componentes técnicos, lead guiado com imagem e sem parâmetros técnicos, contato empresarial sem anexo, persistência do briefing B2B, erros no modo de origem, bloqueio CSRF, imagens dos 18 produtos, páginas individuais, carrinho e acesso ao checkout. JavaScript guiado aprovado em node --check. Não foram efetuados pagamentos.

## Pendências

- O navegador retornou ERR_BLOCKED_BY_CLIENT ao abrir a prévia local. Nenhuma tentativa de contornar o bloqueio, publicar ou acessar serviços externos foi feita.
- Comparação visual real entre os três modos e revisão em 375, 390, 430 px e desktop continuam obrigatórias antes da aprovação.
- Conferência visual do catálogo, produtos e checkout também pendente.
- O ambiente virtual perdeu seu executável; testes rodaram com Python 3.12 e as dependências locais já existentes, sem instalação.
- Sem migration nova, sem variável de ambiente nova e sem alterações em infraestrutura, financeiro ou distribuição.
- Publicação somente após revisão e autorização separada.

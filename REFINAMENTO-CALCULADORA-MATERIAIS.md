# Refinamento — calculadora e compatibilidade de materiais

## Calculadora
- A página de orçamento foi reorganizada como configurador em 5 blocos: tipo de peça, dimensões, material, perfil de impressão e contato.
- O resumo da simulação fica separado e acompanha preço, tempo, peso e configuração atual.
- A composição do preço ficou recolhida por padrão para reduzir poluição visual.
- Materiais agora têm contexto de processo (FDM/SLA-MSLA), indicação de uso e aviso de disponibilidade.
- O valor continua sendo simulação sujeita a confirmação antes da produção.

## Marketplace / materiais
- `pedidos.material_requisito` guarda o material escolhido no orçamento.
- `impressoras.materiais` guarda os materiais que cada parceira declarou produzir.
- A fila automática só considera impressoras online/ativas que suportem o material exigido pelo orçamento e, entre elas, continua priorizando proximidade.
- Cadastro e painel da impressora receberam seleção/editoração de materiais.
- Pedidos de catálogo continuam sem material obrigatório porque o catálogo ainda não possui variantes de material/cor.

## Marca
- O símbolo anterior em V foi substituído por um cubo/hexágono fragmentado com eixos em X sobrepostos e profundidade 3D.
- Foram atualizados `voxxel-mark.svg`, `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png` e `voxxel-avatar.png`.

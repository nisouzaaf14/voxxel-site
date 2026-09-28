# Fluxo de projetos e conversa — Voxxel

Esta versão transforma o orçamento personalizado em um projeto acompanhado pela plataforma.

## Regras de entrada

- A descrição guiada é obrigatória: o que o cliente precisa, o que deve ser respeitado e como a peça será usada.
- O cliente precisa anexar pelo menos um arquivo 3D (STL/3MF/OBJ) **ou** uma imagem de referência (JPG/PNG/WEBP).
- É possível anexar até seis imagens de referência no envio inicial.
- Arquivo 3D + imagens + briefing podem ser usados juntos.
- Referências ficam vinculadas ao pedido e só podem ser abertas pelo cliente dono, pela impressora atribuída ou pelo admin.

## Análise antes da produção

Aceitar uma oferta da Rede Voxxel agora significa **aceitar para análise**, não aceitar produção automaticamente.

Fluxo principal:

1. Pedido recebido
2. Impressora aceita para análise
3. Em análise
4. Parceira pode solicitar esclarecimentos pelo chat
5. Cliente responde e pode anexar novas imagens/arquivos 3D
6. Parceira marca o projeto como compreendido e envia para aprovação
7. Cliente aprova
8. Com pagamento informado, a parceira pode iniciar produção
9. Em produção
10. Pronto
11. Concluído

## Chat do projeto

- Cliente, parceira e admin compartilham a conversa vinculada ao pedido.
- Mensagens podem conter texto, imagem ou arquivo 3D.
- Mensagens não lidas aparecem nos painéis.
- Enquanto o usuário estiver logado no site, novas mensagens geram um aviso global que leva direto para o projeto.
- A tela de projeto verifica novas mensagens automaticamente e atualiza a conversa.

## Segurança dos arquivos

- Nomes são normalizados antes de armazenamento.
- Imagens são validadas pelo conteúdo usando Pillow.
- 3MF é verificado como pacote ZIP 3MF mínimo.
- OBJ recebe uma validação estrutural básica.
- STL recebe uma validação estrutural básica e limite de tamanho.
- Arquivos são guardados no banco e servidos apenas depois de validar acesso ao pedido.

## Observação importante

O status de pagamento `informado` ainda segue a lógica já existente do projeto. Para uma operação financeira em escala, o passo seguinte recomendado é separar claramente pagamento apenas informado de pagamento efetivamente conciliado/confirmado, especialmente no Pix manual.

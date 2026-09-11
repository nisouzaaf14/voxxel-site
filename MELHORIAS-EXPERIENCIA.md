# Voxxel — revisão da experiência do cliente

## Alterações

- Carrinho preservado ao entrar ou criar conta.
- Quantidade editável no carrinho, subtotal por item e link para o produto.
- Revisão de disponibilidade no carrinho e antes da criação do pedido; alterações exigem nova conferência do cliente.
- Produtos sem estoque definido exibem disponibilidade e prazo a confirmar.
- Checkout com recebimento a combinar, interesse em retirada ou consulta de entrega.
- Endereço obrigatório para consulta de entrega e campo opcional de observações. Informações são gravadas nos detalhes do pedido, visíveis também ao administrador.
- Subtotal explicitamente sem frete. Para entrega ou recebimento a combinar, pagamento é combinado após confirmar valores e prazo.
- Pix e cartão só aparecem no checkout quando configurados.
- Localização solicitada apenas após ação do cliente.
- Link de WhatsApp usa o número das configurações quando o chat não está conectado. Nenhuma mensagem é enviada automaticamente.
- Confirmação do pedido com acompanhamento e contato; pagamento informado não é apresentado como conferido.
- Ajustes de legibilidade, foco do teclado, mensagens e disposição dos controles em telas pequenas.

## Uso

Mantenha a estrutura da pasta `voxxel` e o processo de execução descrito no README. Esta revisão mantém Flask, as rotas e os bancos SQLite/PostgreSQL. Não exige migração de tabelas. O pacote não inclui banco local nem pedidos de teste.

## Verificação

Testes locais com banco temporário: renderização das páginas; inclusão e alteração de quantidades; valores inválidos; cadastro e login preservando o carrinho; endereço de entrega; persistência do recebimento e observações; pagamento a combinar; redução do estoque e desativação do produto antes de finalizar.

A inspeção visual no navegador ficou pendente: o navegador de testes não estava instalado e o download não completou. Os testes acima verificaram o servidor e o HTML gerado; a sintaxe JavaScript do chat também foi validada. Pagamentos reais e entrega não foram executados.

## Próximas etapas

Esta revisão trata da experiência de compra. Permanecem para a etapa de operação: reserva/baixa transacional de estoque; validação completa de pagamentos; fila de parceiros independente de visitas e condicionada à autorização para produzir; configuração obrigatória de segredos em produção. Galeria e variantes por cor/material ainda não foram implementadas. Frete e prazo dependem de confirmação manual.


## Revisão visual

Página inicial refeita com composição de duas colunas, ilustração conceitual de impressão 3D, categorias clicáveis, processo de atendimento e convite à rede de parceiros. Identidade roxa e grafite, títulos em Sora, cartões e botões arredondados, cabeçalho mais leve e rodapé com navegação. Catálogo, produto, carrinho e checkout recebem a mesma linguagem visual e regras de adaptação para celular.

A imagem da abertura é uma ilustração gerada, identificada como conceitual, e não representa itens específicos à venda. Prompt: composição de estúdio grafite com vaso roxo estriado, organizador grafite e escultura roxa em laço, camadas FDM visíveis, sem texto ou logotipos. Produzida pela ferramenta de imagens integrada. O arquivo está em static/images/hero-voxxel.png. Fotos reais dos produtos cadastrados continuam sendo utilizadas; itens sem imagem mostram um aviso explícito.

Os testes locais de compra e renderização das páginas foram repetidos após as alterações e passaram. Inspeção visual no navegador continua pendente. Nenhuma publicação foi realizada.


## Revisão a partir da gravação de tela

A gravação enviada mostra uma versão anterior ao redesign deste pacote, com chamadas de precisão e materiais que não constam na nova página inicial. A revisão foi aplicada ao projeto mais recente, preservando o redesign e as melhorias do carrinho.

- Chamada principal: “Sua ideia vira uma peça 3D.”
- Categorias e processo de compra com frases curtas e orientações concretas.
- Sem prometer tolerância dimensional, ausência de marcas de camada ou padrão idêntico entre parceiros.
- Cadastro do parceiro separado da conta do cliente nos textos e links de acesso.
- Orçamento descrito como simulação sujeita à confirmação; campos de dimensão com rótulos individuais.
- Produtos sem foto orientam o cliente a pedir uma referência.
- Mensagem de erro de login mais explicativa, mantendo a autenticação existente.
- Campo do chat pode encolher no celular sem empurrar o botão Enviar para fora.
- Espaço adicional no rodapé e âncoras ajustadas para o cabeçalho fixo.

A gravação permitiu conferir a apresentação da versão filmada. Ela não equivale a uma revisão no navegador da nova versão. O pacote não foi publicado.

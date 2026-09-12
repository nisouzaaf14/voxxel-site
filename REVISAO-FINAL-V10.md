# Voxxel — revisão final V10

## Identidade e experiência
- Hero principal restaurado para **“Sua ideia ganha forma.”**.
- A proposta de Rede Voxxel permanece logo abaixo do hero, sem substituir a mensagem principal da marca.
- Cabeçalho público sem atalho administrativo; o admin continua disponível em `/admin/login`.
- Cliente e parceiro mantêm a mesma linguagem visual de cards, timelines, superfícies e CTAs da home e do fluxo comercial.
- Referências da calculadora continuam exigindo descrição guiada + arquivo 3D ou imagem.
- Removidos emojis de teclado remanescentes da interface/chat.

## Segurança
- Removido integralmente o modo/chave mestra de teste, rotas, template, credenciais e referências de CSS.
- Nenhuma senha administrativa padrão fica no código.
- `VOXXEL_ADMIN_PASSWORD` é obrigatória para o login admin funcionar.
- `VOXXEL_SECRET_KEY` não possui valor conhecido no repositório.
- Pedidos antigos sem cliente associado não ficam mais acessíveis por URL pública previsível.
- Geolocalização foi liberada apenas para a própria origem no `Permissions-Policy`, necessária para o roteamento da rede.
- Mantidos CSRF, cookies HttpOnly/SameSite/Secure, CSP, rate limiting e validação de uploads.

## Pagamentos
- Separados os estados `aguardando`, `informado` e `confirmado`.
- Pix: o cliente apenas informa; o admin confirma manualmente.
- Mercado Pago aprovado: entra como `confirmado` após consulta à API.
- O parceiro só pode iniciar produção com projeto autorizado **e** pagamento confirmado.
- Painéis de cliente, parceiro e admin refletem os três estados corretamente.

## Rede de parceiros
- Uma impressora não recebe duas ofertas simultâneas enquanto uma chamada válida está pendente.
- O pop-up pré-aceite recebe somente um resumo seguro do projeto; endereço e observações privadas não são expostos antes da atribuição.
- Depois do aceite para análise, o fluxo continua por projeto/chat/aprovação antes da produção.

## Validação
- Todos os módulos Python compilam sem erro de sintaxe.
- Todos os templates Jinja foram analisados sem erro de sintaxe.
- Todos os arquivos JavaScript passaram em `node --check`.
- Todas as referências de arquivos estáticos existentes nos templates foram verificadas.
- As rotas referenciadas por `url_for` foram comparadas com as rotas Flask existentes.

## Limites conhecidos
- Não foi possível executar o servidor Flask completo neste ambiente porque as dependências Flask/Werkzeug não estão instaladas e o ambiente não possui acesso de rede para instalá-las.
- A reserva transacional de estoque por pedido ainda não foi implementada; o site continua comunicando disponibilidade sujeita a confirmação.
- Notificação com o navegador totalmente fechado ainda exige Web Push real; a implementação atual cobre site aberto em outra aba/segundo plano.

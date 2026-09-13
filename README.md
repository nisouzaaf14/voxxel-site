# Voxxel — site + loja + orçamento (Flask)

## Como rodar

1. Instale as dependências:
   ```
   pip install -r requirements.txt
   ```

2. Rode o servidor:
   ```
   python app.py
   ```

3. Acesse http://127.0.0.1:5000

O banco de dados SQLite (`voxxel.db`) é criado automaticamente na primeira
execução, já com os 9 produtos de exemplo.

## Painel administrativo

Acesse `http://127.0.0.1:5000/admin/login`.

Não existe senha mestra nem senha padrão no código. Antes de usar o painel,
defina `VOXXEL_ADMIN_PASSWORD` no ambiente. Em produção, defina também uma
`VOXXEL_SECRET_KEY` longa e aleatória para assinar as sessões.

No painel você pode:
- Cadastrar, editar, ativar/desativar e excluir produtos da loja
- Ver todos os pedidos (tanto da loja quanto os orçamentos enviados) e mudar o status deles
- Ver, aprovar, pausar e reativar parceiros cadastrados (aba "Parceiros")
- Atribuir manualmente um parceiro compatível a um pedido, quando necessário

## Marketplace de impressão (parceiros Voxxel)

O site funciona como um "iFood de impressão 3D": uma pessoa ou negócio com capacidade de produção 3D pode se cadastrar em `/impressora/cadastro`, ficar online
(compartilhando a localização do navegador) e passar a receber ofertas de
pedidos feitos por clientes próximos. O parceiro vê a oferta no painel (`/impressora/painel`) e tem 5 minutos pra aceitar ou recusar — se
recusar (ou não responder a tempo), o pedido é automaticamente oferecido
para o próximo parceiro compatível e disponível.

Detalhes técnicos e decisões de design estão comentados em `distribuicao.py`.
Resumo:
- A localização do cliente é capturada (com permissão do navegador) no
  checkout e no orçamento; sem ela, o pedido não entra na fila automática e
  fica aguardando roteamento/atribuição manual.
- A distância é calculada em linha reta (fórmula de Haversine) — não é a
  distância real de rota, mas é suficiente pra ordenar "quem está mais perto".
- Enquanto o MVP não usa um worker dedicado, o avanço da fila é oportunista:
  parceiros online e telas operacionais expiram ofertas antigas e tentam a
  próxima impressora. Para alto volume/múltiplas instâncias, a recomendação é
  migrar isso para uma fila/worker dedicado.

## Estrutura

```
app.py            -> rotas Flask, autenticação, checkout, projetos e admin
database.py       -> SQLite/PostgreSQL, schema, transações e consultas
distribuicao.py   -> matching e fila da rede de impressoras
calculadora.py    -> precificação do orçamento
mercadopago_pay.py -> Checkout Pro e validação de pagamentos
pix.py            -> payload e QR Pix
templates/        -> páginas HTML (Jinja2)
static/css/       -> estilo do site
voxxel.db         -> banco de dados (criado automaticamente)
```

## Observações de produção
- O acesso administrativo depende exclusivamente da variável `VOXXEL_ADMIN_PASSWORD`; não há credencial padrão versionada.
- Pagamento Pix informado pelo cliente fica **em conferência**; só o admin pode confirmá-lo. Cartão aprovado pelo Mercado Pago entra como confirmado.
- A produção só pode ser iniciada depois de projeto autorizado e pagamento confirmado.
- Notificações do parceiro funcionam enquanto o site estiver aberto em alguma aba. Push com o navegador totalmente fechado exige Web Push com assinatura do dispositivo.
- Para produção, use PostgreSQL e configure `DATABASE_URL`; SQLite é adequado apenas para desenvolvimento local.


## Auditoria técnica atual

A revisão consolidada mais recente está documentada em
`AUDITORIA-PROFISSIONAL-V12.md`, com correções aplicadas, testes executados,
limites conhecidos e checklist de publicação.

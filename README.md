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

Acesse http://127.0.0.1:5000/admin/login

Senha padrão: `voxxel123`
(troque definindo a variável de ambiente `VOXXEL_ADMIN_PASSWORD` antes de rodar)

No painel você pode:
- Cadastrar, editar, ativar/desativar e excluir produtos da loja
- Ver todos os pedidos (tanto da loja quanto os orçamentos enviados) e mudar o status deles
- Ver e bloquear/desbloquear impressoras parceiras cadastradas (aba "Impressoras")
- Atribuir manualmente uma impressora a um pedido, quando ninguém aceitou automaticamente

## Marketplace de impressão (impressoras parceiras)

O site funciona como um "iFood de impressão 3D": qualquer pessoa com uma
impressora pode se cadastrar em `/impressora/cadastro`, ficar online
(compartilhando a localização do navegador) e passar a receber ofertas de
pedidos feitos por clientes próximos. A impressora vê a oferta no painel
dela (`/impressora/painel`) e tem 5 minutos pra aceitar ou recusar — se
recusar (ou não responder a tempo), o pedido é automaticamente oferecido
pra próxima impressora online mais próxima do cliente.

Detalhes técnicos e decisões de design estão comentados em `distribuicao.py`.
Resumo:
- A localização do cliente é capturada (com permissão do navegador) no
  checkout e no orçamento; sem ela, o pedido não entra na fila de despacho
  e fica marcado pra produção direta pela Voxxel.
- A distância é calculada em linha reta (fórmula de Haversine) — não é a
  distância real de rota, mas é suficiente pra ordenar "quem está mais perto".
- Como o site roda num único processo Flask sem worker em segundo plano,
  o avanço da fila (expirar oferta vencida, tentar a próxima impressora) é
  "preguiçoso": acontece sempre que alguém abre uma tela que depende disso
  (painel da impressora, painel do admin, página de pagamento do pedido).

## Estrutura

```
app.py            -> rotas Flask (páginas, carrinho, orçamento, admin)
database.py       -> conexão SQLite + criação das tabelas + produtos iniciais
calculadora.py    -> lógica de precificação do orçamento (usada pelo servidor)
templates/        -> páginas HTML (Jinja2)
static/css/       -> estilo do site
voxxel.db         -> banco de dados (criado automaticamente)
```

## Próximos passos possíveis
- Trocar a senha fixa do admin por login de verdade (usuário/senha com hash)
- Upload de imagens reais dos produtos em vez do placeholder facetado
- Enviar o pedido automaticamente por e-mail além do WhatsApp
- Deploy em um servidor (Render, Railway, PythonAnywhere etc.)

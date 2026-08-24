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

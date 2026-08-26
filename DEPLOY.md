# Como colocar a Voxxel no ar (Render — gratuito)

O jeito mais simples de publicar esse site é o **Render**: ele conecta direto
no GitHub, detecta que é um projeto Flask e sobe sozinho toda vez que você
atualizar o código.

## Passo 1 — Colocar o código no GitHub

1. Crie uma conta em https://github.com (se ainda não tiver).
2. Crie um repositório novo, por exemplo `voxxel-site`. Pode deixar privado.
3. Envie os arquivos desse projeto pra esse repositório. Se você tem o Git
   instalado no seu computador, dentro da pasta do projeto:
   ```
   git init
   git add .
   git commit -m "primeira versão do site"
   git branch -M main
   git remote add origin https://github.com/SEU-USUARIO/voxxel-site.git
   git push -u origin main
   ```
   Se preferir, dá pra fazer isso direto pela interface do GitHub também
   (botão "Add file" → "Upload files"), sem usar linha de comando.

## Passo 2 — Criar o serviço no Render

1. Crie uma conta em https://render.com (dá pra entrar direto com o GitHub).
2. Clique em **New +** → **Web Service**.
3. Selecione o repositório `voxxel-site` que você acabou de criar.
4. Preencha:
   - **Name**: `voxxel` (ou o que preferir — vira parte da URL)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: Free
5. Em **Environment Variables**, adicione:
   - `VOXXEL_ADMIN_PASSWORD` → uma senha forte sua (troque o `voxxel123`)
   - `VOXXEL_SECRET_KEY` → qualquer texto longo e aleatório
   - `VOXXEL_DEBUG` → `false`
6. Clique em **Create Web Service**.

Em alguns minutos o Render te dá uma URL tipo `https://voxxel.onrender.com` —
esse já é o site no ar, pronto pra você mandar pros clientes.

## Banco de dados: usando Postgres (recomendado)

O `database.py` já está preparado para os dois modos:
- **Sem** a variável `DATABASE_URL` → usa SQLite local (`voxxel.db`), bom só
  pra testar rápido no seu computador.
- **Com** `DATABASE_URL` → usa Postgres automaticamente, com dados
  permanentes (não some quando o serviço reinicia).

### Passo a passo no Render

1. No painel do Render, clique em **New +** → **PostgreSQL**.
   - Dê um nome (ex: `voxxel-db`), deixe o plano **Free**, e crie.
   - Espere o banco ficar com status "Available" (leva 1-2 minutos).
2. Volte no seu **Web Service** (`voxxel`) → aba **Environment**.
3. Clique em **Add Environment Variable** e escolha a opção de **linkar um
   banco existente** ("Link a database" / "Add from Database") — o Render
   preenche `DATABASE_URL` sozinho com a Internal Database URL do banco que
   você criou. (Se essa opção não aparecer na sua versão do painel, copie a
   **Internal Database URL** da página do banco Postgres e cole manualmente
   como variável `DATABASE_URL` no Web Service.)
4. Clique em **Save Changes** — o Render reimplanta o serviço sozinho.
5. Pronto: na próxima subida, o site cria as tabelas e os 9 produtos de
   exemplo dentro do Postgres, e esses dados agora **persistem** entre
   reinicializações e deploys.

> Atenção: o Postgres free do Render expira depois de um tempo (atualmente
> ~30 dias de banco gratuito, verifique as condições atuais no site deles).
> Depois disso ele cobra um valor baixo mensal pra manter o banco ativo.

### Como confirmar que está usando Postgres de verdade

Depois do deploy, abra a aba **Logs** do seu Web Service no Render e procure
pela primeira linha que o site imprime ao iniciar:
- `[BANCO DE DADOS] Usando PostgreSQL (dados permanentes).` → certo, configurado.
- `[BANCO DE DADOS] Usando SQLite local -- ATENÇÃO...` → a variável
  `DATABASE_URL` não foi encontrada; revise o passo 3 acima.

### Detalhes técnicos (pra quem quiser saber o que roda por baixo)

O `database.py` já vem preparado pra produção de verdade, não só pra
funcionar no teste:
- **Pool de conexões**: reaproveita conexões com o Postgres em vez de abrir
  uma nova a cada clique no site -- importante porque planos free costumam
  limitar bastante o número de conexões simultâneas.
- **Reconexão automática**: se o banco estiver "acordando" ou a conexão
  cair por um instante, o site tenta de novo (com espera crescente) antes
  de mostrar erro.
- **SSL obrigatório** na conexão com o Postgres.
- **Trava contra duplicação**: se um dia você aumentar os workers do
  gunicorn, o site usa uma trava do próprio Postgres pra garantir que a
  criação de tabelas/produtos de exemplo não rode em duplicidade.

### Se preferir continuar só com SQLite por enquanto

Não precisa fazer nada — sem a variável `DATABASE_URL`, o site continua
funcionando com SQLite normalmente (só que sem persistir dados no plano
free do Render, como explicado antes).

## Alternativas ao Render

- **PythonAnywhere** — também tem plano grátis, é um pouco mais manual de
  configurar mas o disco é permanente mesmo no free.
- **Railway** — parecido com o Render, também baseado em GitHub.

Se quiser, me diz qual você escolheu que eu ajusto as instruções certinho
pra ela.

## Segurança — checklist antes de divulgar o site

O código já vem com várias proteções (proteção contra CSRF, limite de
tentativas de login, cabeçalhos de segurança no navegador, validação de
imagens enviadas, cookies seguros, etc). Mas duas coisas **dependem de
você configurar** na hora do deploy:

1. **`VOXXEL_ADMIN_PASSWORD`** — troque a senha padrão (`voxxel123`) por
   uma senha forte e única. É a senha que protege o painel inteiro.
2. **`VOXXEL_SECRET_KEY`** — defina qualquer texto longo e aleatório
   (ex: gere um em https://randomkeygen.com, categoria "CodeIgniter
   Encryption Keys"). Sem isso, os cookies de sessão do site usam uma
   chave conhecida publicamente (documentada aqui mesmo), o que é
   inseguro.

Sem essas duas variáveis configuradas, o site imprime um aviso nos logs
do Render toda vez que inicia, lembrando de trocar.

Outras variáveis relacionadas à segurança (opcionais):
- `VOXXEL_DEBUG` → deixe `false` em produção (é o padrão). Nunca ligue o
  modo debug num site publicado — ele expõe informações internas e
  permite executar código no servidor por quem encontrar uma página de
  erro.
- `VOXXEL_COOKIE_SECURE` → normalmente não precisa mexer: o site já detecta
  sozinho se está rodando publicado (Render) ou só testando no seu
  computador. Só use essa variável se notar problemas de sessão/login.

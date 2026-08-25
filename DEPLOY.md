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

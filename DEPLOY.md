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

## Um ponto de atenção: o banco de dados

O plano gratuito do Render **não guarda arquivos de forma permanente** —
toda vez que o serviço reinicia (o que acontece de tempos em tempos no plano
free), o arquivo `voxxel.db` volta ao estado inicial, com os 9 produtos de
exemplo, e você perde produtos/pedidos que tiver cadastrado depois.

Isso não é um problema pra testar e mostrar o site agora. Mas quando você
for usar de verdade com clientes, tem duas saídas simples:
- Assinar o **Disco Persistente** do Render (pago, mas barato) pra manter o
  `voxxel.db` entre reinicializações;
- Ou trocar o SQLite por um banco de dados online gratuito (o próprio Render
  oferece PostgreSQL grátis por um tempo) — isso exige um ajuste no
  `database.py`, e posso te ajudar quando chegar a hora.

## Alternativas ao Render

- **PythonAnywhere** — também tem plano grátis, é um pouco mais manual de
  configurar mas o disco é permanente mesmo no free.
- **Railway** — parecido com o Render, também baseado em GitHub.

Se quiser, me diz qual você escolheu que eu ajusto as instruções certinho
pra ela.

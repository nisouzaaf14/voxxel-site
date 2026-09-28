# Como colocar a Voxxel no ar (Render)

Uma opção prática para publicar a Voxxel é o **Render**: ele conecta ao
GitHub e pode fazer o deploy do Flask a cada atualização. Planos, limites e
preços mudam com o tempo; confira as condições atuais no painel do provedor.

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
   - **Instance Type**: escolha o plano adequado ao ambiente/teste
5. Em **Environment Variables**, adicione:
   - `VOXXEL_ADMIN_PASSWORD` → uma senha forte e única (não existe senha padrão no código)
   - `VOXXEL_SECRET_KEY` → qualquer texto longo e aleatório
   - `VOXXEL_DEBUG` → `false`
   - `VOXXEL_TRUST_PROXY` → `true` no Render/proxy confiável
   - `VOXXEL_CHAT_WEBHOOK_URL` → opcional; URL HTTPS do webhook de atendimento, se utilizado
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

> Os planos, políticas de retenção e preços do PostgreSQL no Render podem
> mudar. Confirme as condições atuais antes de depender de um plano específico.

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
  uma nova a cada clique no site, reduzindo custo e pressão sobre o banco.
- **Reconexão automática**: se o banco estiver "acordando" ou a conexão
  cair por um instante, o site tenta de novo (com espera crescente) antes
  de mostrar erro.
- **SSL obrigatório** na conexão com o Postgres.
- **Trava contra duplicação**: se um dia você aumentar os workers do
  gunicorn, o site usa uma trava do próprio Postgres pra garantir que a
  criação de tabelas/produtos de exemplo não rode em duplicidade.

### Se preferir continuar só com SQLite por enquanto

Não precisa fazer nada — sem a variável `DATABASE_URL`, o site continua
funcionando com SQLite local. Em hospedagens com filesystem efêmero, porém,
esses dados podem não sobreviver a deploys/reinicializações; confirme a
política do provedor antes de usar SQLite em produção.

## Alternativas ao Render

- **PythonAnywhere** — alternativa de hospedagem Python com configuração mais manual.
- **Railway** — alternativa com deploy integrado a repositórios Git.

Planos, armazenamento e preços desses serviços mudam; consulte a documentação
atual antes de escolher a infraestrutura.

Se quiser, me diz qual você escolheu que eu ajusto as instruções certinho
pra ela.

## Segurança — checklist antes de divulgar o site

O código já vem com várias proteções (proteção contra CSRF, limite de
tentativas de login, cabeçalhos de segurança no navegador, validação de
imagens enviadas, cookies seguros, etc). Mas duas coisas **dependem de
você configurar** na hora do deploy:

1. **`VOXXEL_ADMIN_PASSWORD`** — defina uma senha forte e única. Sem essa
   variável o login administrativo fica desabilitado; não existe fallback
   ou senha mestra no repositório.
2. **`VOXXEL_SECRET_KEY`** — defina um valor longo e aleatório. Em ambiente
   de produção a aplicação recusa iniciar sem essa variável; localmente uma
   chave efêmera ainda pode ser usada para desenvolvimento.

Sem essas duas variáveis configuradas, o site imprime um aviso nos logs
do Render ao iniciar.

Outras variáveis relacionadas à segurança (opcionais):
- `VOXXEL_DEBUG` → deixe `false` em produção (é o padrão). Nunca ligue o
  modo debug num site publicado — ele expõe informações internas e
  permite executar código no servidor por quem encontrar uma página de
  erro.
- `VOXXEL_COOKIE_SECURE` → normalmente não precisa mexer: o site já detecta
  sozinho se está rodando publicado (Render) ou só testando no seu
  computador. Só use essa variável se notar problemas de sessão/login.


## Health check

O endpoint `/health` verifica aplicação e banco. Configure o monitoramento do
serviço para consultar esse caminho. Resposta saudável: HTTP 200 com `ok=true`.

## Antes de liberar vendas

Leia `AUDITORIA-PROFISSIONAL-V12.md`. Além do deploy, faça um ensaio completo
no domínio publicado: conta de cliente, conta de parceiro, geolocalização,
referências, chat, aprovação, Pix/Mercado Pago, início e conclusão da produção.

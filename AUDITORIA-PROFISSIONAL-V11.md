# Voxxel — Auditoria profissional V11

Esta versão consolida uma revisão de arquitetura, backend, banco, segurança,
pagamentos, marketplace, UX, acessibilidade e frontend. O objetivo foi tratar
os fluxos como produto real, e não apenas como demonstração visual.

## Escopo revisado

- Home, catálogo, produto, carrinho, checkout e pagamento.
- Cadastro/login e área do cliente.
- Cadastro/login, disponibilidade, ofertas e área da impressora parceira.
- Projeto personalizado: referências, chat, aprovação e produção.
- Painel administrativo, produtos, pedidos, impressoras e configurações.
- Banco SQLite/PostgreSQL e transações críticas.
- Distribuição por proximidade/material.
- Pix e Mercado Pago.
- Upload de imagens/modelos 3D/anexos.
- CSRF, sessões, permissões, headers e rate limiting.
- Responsividade, acessibilidade, consistência visual e textos.

## Correções críticas realizadas

### Pagamentos e autorização de produção

- Retorno do Mercado Pago não confia em `status=approved` da URL.
- Confirmação consulta o pagamento pela API e valida pedido, moeda e valor
  exato até o centavo.
- Webhook aplica a mesma validação antes de confirmar pagamento.
- Pix marcado pelo cliente fica apenas **em conferência**; não autoriza
  produção automaticamente.
- Projetos personalizados só liberam pagamento depois da aprovação final.
- Produção exige simultaneamente projeto autorizado e pagamento confirmado.
- Valor final do projeto pode ser definido no envio para aprovação.

### Estoque e pedidos de catálogo

- A baixa de estoque passou a fazer parte da mesma transação que cria o
  pedido: se uma unidade acabar no meio do checkout, o pedido inteiro é
  revertido.
- Cada pedido salva um snapshot dos itens em `pedido_itens`.
- Produto passou a ter material de produção real.
- Carrinho com materiais diferentes só é oferecido a uma parceira que suporte
  todos eles.

### Rede de impressoras

- Impressora só participa da fila com localização recente.
- Localização expirada retira a parceira da disponibilidade.
- Uma oferta deixa de prender a fila quando a parceira fecha o navegador,
  fica offline ou perde o heartbeat.
- Qualquer parceiro ativo consultando a rede ajuda a avançar ofertas vencidas
  enquanto o MVP não possui worker dedicado.
- Uma impressora não recebe duas chamadas pendentes simultâneas.
- Aceite de oferta usa atualizações condicionais para reduzir corrida de
  concorrência.
- Pedido de catálogo aceito pode seguir como produção autorizada; projeto
  personalizado sempre entra primeiro em análise.
- Antes do aceite, a oferta mostra apenas um resumo seguro do pedido e não
  expõe endereço/contato/observações privadas.

### Banco e integridade

- Conexões PostgreSQL são revertidas antes de retornar ao pool quando
  necessário, evitando contaminar a próxima requisição.
- Foram adicionados índices para pedidos, pagamentos, impressoras, ofertas e
  itens do pedido.
- Campos numéricos rejeitam `NaN`/`Infinity` e respeitam limites coerentes.
- Coordenadas são validadas dentro de latitude/longitude reais.

### Segurança

- Não há chave mestra, conta de teste privilegiada ou senha administrativa
  padrão no repositório.
- `VOXXEL_SECRET_KEY` passa a ser obrigatória em ambiente de produção; o app
  não sobe silenciosamente com uma chave efêmera.
- `ProxyFix` só é habilitado quando o ambiente está configurado como proxy
  confiável.
- CSRF continua obrigatório em ações POST, com exceção do webhook externo.
- Logout de cliente, parceiro e admin é POST.
- Cookies são HttpOnly, SameSite e Secure quando publicados em HTTPS.
- Rotas privadas recebem `no-store` e `noindex`.
- Headers incluem CSP, HSTS em HTTPS, COOP/CORP e bloqueios adicionais.
- Uploads são inspecionados e limitados; 3MF possui proteção contra arquivo
  compactado abusivo e imagens têm limite de pixels.
- Login possui limitação de tentativas e limpeza oportunística da memória.

### Erros e operação

- 403, 404 e 500 usam página Voxxel em vez da tela padrão do Flask.
- Existe `/health` para monitoramento de aplicação/banco.
- Página de erro continua renderizando mesmo se o banco estiver indisponível.
- Páginas privadas não devem ficar em cache compartilhado.

## Frontend, UX e acessibilidade

- Mantido o hero principal **“Sua ideia ganha forma.”**.
- Identidade preta/grafite + roxo Voxxel aplicada de forma consistente.
- Fluxos cliente e parceiro usam a mesma linguagem visual e timelines.
- Acompanhamento mostra etapa atual, concluídas e próximo passo.
- Painel do parceiro prioriza itens que exigem ação.
- Imagem principal da home ganhou versão WebP otimizada com fallback PNG.
- Foram revisados labels, autocomplete, `alt`, navegação, foco e skip link.
- Há tratamento para `prefers-reduced-motion` e responsividade nos principais
  layouts.

## Validações executadas nesta revisão

- Compilação de todos os módulos Python (`py_compile`).
- Parse de todos os templates Jinja.
- Verificação sintática de todos os JavaScripts com Node.
- Varredura de formulários POST sem token CSRF.
- Varredura de `url_for` contra endpoints conhecidos.
- Varredura de imagens sem `alt` e links `_blank` sem `noopener`.
- Busca por credenciais/chaves de teste conhecidas no repositório.
- Testes de integração do núcleo em banco SQLite temporário:
  - compatibilidade de materiais;
  - fluxo de projeto personalizado;
  - despacho misto PLA/PETG;
  - expiração/reoferta por parceiro offline;
  - autorização de catálogo;
  - validação exata de pagamento;
  - geração Pix com arredondamento monetário;
  - schema `pedido_itens` e material do produto.
- Teste de reserva transacional de estoque e rollback.
- Testes de rejeição de valores não finitos.

## Limites conhecidos antes de escala maior

Estes itens não são bugs escondidos; são evoluções arquiteturais recomendadas
antes de transformar o MVP em uma operação de alto volume:

1. **Worker/fila real** — hoje o despacho avança de forma oportunista. Para
   múltiplos workers e alto volume, usar Redis + RQ/Celery ou serviço de fila.
2. **Armazenamento de arquivos** — imagens/STL/anexos em BLOB no banco são
   adequados ao MVP, mas em escala devem ir para S3/R2 ou equivalente.
3. **Dinheiro** — o sistema valida pagamentos com `Decimal`, porém o schema
   legado ainda guarda vários valores em `REAL`. Em contabilidade de alto
   volume, migrar para centavos inteiros ou `NUMERIC`.
4. **Migrations formais** — hoje a aplicação atualiza schema na inicialização.
   Em produção madura, usar Alembic ou processo de migration versionado.
5. **Rate limit distribuído** — o limite de login é em memória do processo.
   Redis/DB é preferível ao aumentar workers/instâncias.
6. **Admin** — hoje é protegido por senha de ambiente. Para operação real,
   criar contas administrativas individuais + MFA + trilha de auditoria.
7. **Web Push** — alertas em outra aba funcionam enquanto existe uma aba da
   Voxxel aberta. Navegador totalmente fechado exige push subscription + envio
   pelo servidor.
8. **Capacidade da impressora** — matching usa material, disponibilidade e
   localização. Futuramente deve considerar volume de mesa, tecnologia,
   bico/resolução, cores e outras capacidades.
9. **Slicer real** — orçamento por dimensões é simulação. STL/3MF deveria ser
   analisado/sliceado para estimar tempo, material, suporte e viabilidade.
10. **Cancelamento/estorno/reposição** — estoque agora é reservado com
    segurança no pedido; falta um fluxo formal de cancelamento que devolva
    estoque e trate reembolso.
11. **Frete** — continua sujeito a confirmação; ainda não há motor de frete.
12. **Mercado Pago em produção** — o código foi auditado e testado com dados
    simulados, mas webhooks/retornos precisam de ensaio em ambiente sandbox e
    depois produção real.

## Checklist de publicação

- Definir `VOXXEL_SECRET_KEY` longa e aleatória.
- Definir `VOXXEL_ADMIN_PASSWORD` forte e exclusiva.
- Configurar `DATABASE_URL` PostgreSQL.
- Configurar domínio/HTTPS.
- Configurar chave Pix e, se usado, token Mercado Pago.
- Testar um pagamento real de baixo valor antes de divulgar.
- Testar permissões de geolocalização e notificações em Android/iOS/desktop.
- Fazer um pedido ponta a ponta como cliente e outro como parceiro no domínio
  publicado.
- Conferir envio de imagens, STL/3MF e anexos com arquivos reais.
- Monitorar `/health`, logs e erros nas primeiras vendas.

## Observação sobre validação visual

Nesta auditoria o frontend foi revisado estruturalmente em HTML/CSS/JS,
responsividade e acessibilidade. Este ambiente de trabalho não possui Flask e
suas dependências instaladas e não tem acesso à internet para instalá-las,
portanto não foi possível executar a aplicação completa em navegador contra o
servidor real. A revisão final pixel a pixel deve ser feita no staging após o
deploy; isso é deliberadamente registrado aqui para não confundir validação
estática com teste E2E real.

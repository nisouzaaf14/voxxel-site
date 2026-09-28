# Voxxel — Auditoria Profissional V12

## Escopo
Revisão estrutural do projeto completo: front-end, back-end, UX/UI, arquitetura, marketplace/e-commerce, responsividade, conversão, performance, segurança e identidade visual.

## Resultado executivo
A V12 consolida a Voxxel como uma plataforma de impressão 3D sob demanda, reduzindo repetição na home, fortalecendo os fluxos de cliente e parceiro, melhorando descoberta do catálogo e adicionando proteções de integridade em pedidos, pagamentos, estoque e distribuição.

### Home e identidade
- Hierarquia reestruturada: proposta de valor → caminhos principais → funcionamento → catálogo → diferenciais → parceiros.
- Hero preserva a essência da marca com “Sua ideia ganha forma.” e dois CTAs prioritários.
- Navegação e rodapé reorganizados, com versão de marca apropriada para fundo escuro.
- Terminologia revisada para “parceiro”, “Área do parceiro” e “Torne-se um parceiro”.
- Ícones/controles mantêm linguagem profissional, sem emojis de teclado.

### Catálogo e compra
- Busca por nome/descrição no servidor.
- Categorias, ordenação e paginação reais.
- Estado sem resultados e contagem de resultados.
- Produtos passam a ter material de produção usado pelo matching da rede.
- Pedido registra snapshot dos itens e reserva estoque dentro da transação.
- Cancelamento devolve estoque uma única vez e encerra ofertas pendentes.

### Projeto personalizado
- Briefing, referências e arquivos continuam obrigatórios de forma coerente com o fluxo.
- Imagens recebidas são validadas, redimensionadas e reencodadas em WebP sem EXIF.
- Configurações da calculadora são serializadas com `tojson`.
- O fluxo diferencia análise, esclarecimento, aprovação, pagamento e produção.

### Cliente e acompanhamento
- Timeline compartilhada evita estados técnicos e discrepâncias entre telas.
- Estados de cancelamento e pagamento recusado/pendente são tratados explicitamente.
- Cliente sempre recebe contexto de etapa atual e próximo passo.

### Parceiros
- Cadastro novo entra como pendente e inativo até aprovação administrativa.
- Parceiro não aprovado não pode ficar online nem receber trabalhos.
- Painel usa nomenclatura e prioridades operacionais mais claras.
- Ganhos excluem pedidos cancelados.
- Distribuição exige compatibilidade com todos os materiais do pedido.

### Marketplace e concorrência
- Índices parciais garantem no banco apenas uma oferta pendente por pedido e por parceiro.
- Aceite concorrente com cancelamento/conclusão é bloqueado.
- Atribuição manual valida estado do pedido, parceiro ativo e compatibilidade de materiais.
- Ofertas concorrentes são expiradas após atribuição.
- Informações privadas permanecem ocultas antes da atribuição.

### Pagamentos
- Pix inválido/oversized é rejeitado no servidor.
- QR Pix não é gerado para pedido cancelado ou fluxo ainda não autorizado.
- Pix informado pelo cliente não equivale a pagamento confirmado.
- Mercado Pago mantém validação server-side de referência, moeda e valor.
- IDs externos são sanitizados antes de consulta.
- Pedido cancelado não oferece novo pagamento; pagamento confirmado em pedido cancelado fica para tratamento de estorno, sem entrar em ganhos/comissão.

### Segurança
- Sem usuário/chave mestra ou senha administrativa padrão.
- Produção exige `VOXXEL_SECRET_KEY`; admin depende de `VOXXEL_ADMIN_PASSWORD`.
- CSRF permanece em formulários POST e ACLs continuam no servidor.
- Uploads são validados; imagens têm metadados removidos.
- CSP inclui bloqueio de frames e mantém HTTPS externo necessário ao webhook opcional de chat.
- Páginas privadas usam no-store/noindex.
- Configuração de webhook de chat saiu do JavaScript hardcoded e agora usa ambiente.

### Performance e acessibilidade
- Fontes deixaram de usar `@import` bloqueante e usam preconnect/link.
- Imagem principal possui versão WebP otimizada.
- Polling global de mensagens pausa com a aba oculta.
- Prevenção de duplo submit em formulários importantes.
- Estados de foco, redução de movimento, labels e navegação por teclado revisados.

## Validações executadas
- Compilação dos módulos Python principais.
- Parse de 26 templates Jinja.
- Validação sintática de todos os JavaScripts com Node.
- Conferência de endpoints usados por `url_for`.
- Conferência de CSRF em formulários POST.
- Conferência de referências estáticas.
- Testes unitários do núcleo: precificação, Pix, onboarding de parceiro, cancelamento/aceite e unicidade de ofertas.

## Limites que ainda merecem evolução antes de grande escala
- `app.py` ainda concentra muitas responsabilidades; a próxima refatoração arquitetural deve separar blueprints/services sem fazer isso às pressas em uma rodada visual.
- Valores monetários ainda devem migrar de REAL/float para centavos inteiros ou NUMERIC.
- Arquivos/BLOBs devem ir para object storage (S3/R2) em escala.
- Rate limit e filas devem usar armazenamento compartilhado/Redis em múltiplas instâncias.
- Matching futuro deve considerar volume de mesa, bico, tecnologia e capacidade além de material/localização.
- Admin deve evoluir para contas individuais + MFA.
- Falta recuperação/verificação de conta e automação formal de estorno.
- O rascunho de projeto não preserva arquivos através de um login iniciado no meio do envio.
- A estimativa de impressão ainda não executa slicing real de STL/3MF.
- Termos e privacidade precisam de revisão jurídica antes de operação comercial definitiva.

## Limitação desta auditoria
Foi possível revisar código, templates, estilos, JavaScript, banco e executar testes automatizados do núcleo. O ambiente desta execução não forneceu uma aplicação Flask completa rodando em staging com navegador, pagamento e geolocalização reais; portanto, antes da publicação definitiva é obrigatório realizar um ensaio ponta a ponta no domínio de staging em desktop e celular, incluindo Mercado Pago sandbox/baixo valor, Pix, permissões de localização e notificações.

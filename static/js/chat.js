// ============================================================
// Chat da Voxxel — conecta com o workflow do N8N
// ============================================================
// 1. Cole a "Production URL" do seu nó Webhook do N8N aqui embaixo,
//    entre as aspas:
const WEBHOOK_URL = "COLE_AQUI_A_URL_DO_SEU_WEBHOOK_N8N";
// Exemplo: "https://seu-n8n.com/webhook/chat-voxxel"
// ============================================================

(function () {
  // Atendimento direto enquanto o chat não estiver conectado.
  if (!WEBHOOK_URL || WEBHOOK_URL.includes("COLE_AQUI")) {
    if (window.VOXXEL_WHATSAPP) {
      const link = document.createElement('a');
      link.className = 'btn btn-primary whatsapp-fallback';
      link.innerHTML = '<img class="whatsapp-brand-mark" src="/static/images/voxxel-whatsapp.png" alt=""><span>Falar no WhatsApp</span>';
      link.target = '_blank'; link.rel = 'noopener';
      link.href = 'https://wa.me/' + window.VOXXEL_WHATSAPP + '?text=' + encodeURIComponent(window.VOXXEL_CHAT_AUTO_MESSAGE || 'Olá! Preciso de ajuda com uma peça da Voxxel.');
      document.body.appendChild(link);
    }
    return;
  }

  // ---------- estilos do widget ----------
  const style = document.createElement("style");
  style.textContent = `
    #voxxel-chat-bubble {
      position: fixed; bottom: 22px; right: 22px; z-index: 999;
      width: 58px; height: 58px; border-radius: 50%;
      background: linear-gradient(135deg, var(--violet, #a259ff), var(--violet-soft, #8b3ff0));
      border: none; cursor: pointer; display: flex; align-items: center; justify-content: center;
      box-shadow: 0 8px 24px rgba(162,89,255,0.45);
      transition: transform .2s var(--ease, ease);
    }
    #voxxel-chat-bubble:hover { transform: scale(1.06); }
    #voxxel-chat-bubble img { width: 38px; height: 38px; display:block; }

    #voxxel-chat-panel {
      position: fixed; bottom: 92px; right: 22px; z-index: 999;
      width: min(360px, calc(100vw - 32px));
      height: min(480px, calc(100vh - 140px));
      background: var(--panel, #140d22);
      border: 1px solid var(--line-strong, rgba(168,133,215,0.32));
      border-radius: var(--radius, 6px);
      display: none; flex-direction: column; overflow: hidden;
      box-shadow: 0 20px 60px rgba(0,0,0,0.5);
      font-family: inherit;
    }
    #voxxel-chat-panel.open { display: flex; }

    #voxxel-chat-header {
      padding: 14px 16px; background: var(--panel-2, #1a1130);
      border-bottom: 1px solid var(--line, rgba(168,133,215,0.16));
      color: var(--text, #f5f1fb); font-weight: 600; font-size: .95rem;
      display: flex; align-items: center; justify-content: space-between;
    }
    #voxxel-chat-close {
      background: none; border: none; color: var(--muted, #a996c4);
      cursor: pointer; font-size: 1.1rem; line-height: 1; padding: 4px;
    }

    #voxxel-chat-messages {
      flex: 1; overflow-y: auto; padding: 14px 16px;
      display: flex; flex-direction: column; gap: 10px;
    }
    .voxxel-msg {
      max-width: 82%; padding: 9px 12px; border-radius: 10px;
      font-size: .87rem; line-height: 1.4; white-space: pre-wrap;
    }
    .voxxel-msg.bot {
      align-self: flex-start; background: var(--panel-2, #1a1130);
      color: var(--text, #f5f1fb); border: 1px solid var(--line, rgba(168,133,215,0.16));
    }
    .voxxel-msg.user {
      align-self: flex-end; background: var(--violet, #a259ff); color: #fff;
    }
    .voxxel-msg.loading { color: var(--muted, #a996c4); font-style: italic; }

    #voxxel-chat-form {
      display: flex; gap: 8px; padding: 12px;
      border-top: 1px solid var(--line, rgba(168,133,215,0.16));
    }
    #voxxel-chat-input {
      flex: 1; min-width: 0; background: var(--panel-2, #1a1130); color: var(--text, #f5f1fb);
      border: 1px solid var(--line, rgba(168,133,215,0.16)); border-radius: var(--radius-sm, 3px);
      padding: 9px 11px; font-size: .87rem; font-family: inherit; resize: none;
    }
    #voxxel-chat-input:focus { outline: 1px solid var(--violet, #a259ff); }
    #voxxel-chat-send {
      background: var(--gold, #cdab73); color: #1a1130; border: none;
      border-radius: var(--radius-sm, 3px); padding: 0 16px; font-weight: 600;
      cursor: pointer; font-size: .87rem;
    }
    #voxxel-chat-send:disabled { opacity: .6; cursor: default; }
  `;
  document.head.appendChild(style);

  // ---------- HTML do widget ----------
  const wrapper = document.createElement("div");
  wrapper.innerHTML = `
    <button id="voxxel-chat-bubble" aria-label="Abrir atendimento Voxxel">
      <img src="/static/images/voxxel-whatsapp.png" alt="">
    </button>

    <div id="voxxel-chat-panel">
      <div id="voxxel-chat-header">
        <span style="display:flex;align-items:center;gap:9px"><img src="/static/images/voxxel-mark.png" alt="" style="width:25px;height:25px">Fale com a Voxxel</span>
        <button id="voxxel-chat-close" aria-label="Fechar chat">✕</button>
      </div>
      <div id="voxxel-chat-messages"></div>
      <form id="voxxel-chat-form">
        <textarea id="voxxel-chat-input" rows="1" placeholder="Digite sua mensagem..."></textarea>
        <button type="submit" id="voxxel-chat-send">Enviar</button>
      </form>
    </div>
  `;
  document.body.appendChild(wrapper);

  // ---------- lógica ----------
  const bubble = document.getElementById("voxxel-chat-bubble");
  const panel = document.getElementById("voxxel-chat-panel");
  const closeBtn = document.getElementById("voxxel-chat-close");
  const messagesEl = document.getElementById("voxxel-chat-messages");
  const form = document.getElementById("voxxel-chat-form");
  const input = document.getElementById("voxxel-chat-input");
  const sendBtn = document.getElementById("voxxel-chat-send");

  let historico = []; // guarda as últimas mensagens da conversa
  let aberto = false;

  function addMessage(texto, autor) {
    const div = document.createElement("div");
    div.className = "voxxel-msg " + autor;
    div.textContent = texto;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function toggle(forceOpen) {
    aberto = forceOpen !== undefined ? forceOpen : !aberto;
    panel.classList.toggle("open", aberto);
    if (aberto && messagesEl.children.length === 0) {
      addMessage("Oi! Sou a assistente da Voxxel 🙂 Como posso ajudar?", "bot");
    }
    if (aberto) input.focus();
  }

  bubble.addEventListener("click", () => toggle());
  closeBtn.addEventListener("click", () => toggle(false));

  async function enviarMensagem(texto) {
    if (!texto) return;

    if (!WEBHOOK_URL || WEBHOOK_URL.includes("COLE_AQUI")) {
      addMessage("O atendimento por chat está indisponível no momento. Entre em contato pelo WhatsApp.", "bot");
      return;
    }

    addMessage(texto, "user");
    historico.push({ autor: "user", texto });
    sendBtn.disabled = true;
    const loadingEl = addMessage("digitando...", "bot loading");

    try {
      const resp = await fetch(WEBHOOK_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mensagem: texto, historico }),
      });
      const data = await resp.json();
      const resposta = data.resposta || data.output || data.text || "Desculpa, não consegui entender agora.";
      loadingEl.remove();
      addMessage(resposta, "bot");
      historico.push({ autor: "bot", texto: resposta });
    } catch (err) {
      loadingEl.remove();
      addMessage("Não consegui falar com o assistente agora. Tenta de novo em instantes.", "bot");
      console.error("Erro no chat da Voxxel:", err);
    } finally {
      sendBtn.disabled = false;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const texto = input.value.trim();
    if (!texto) return;
    input.value = "";
    enviarMensagem(texto);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  // ---------- abertura automática após finalizar pedido/orçamento ----------
  if (window.VOXXEL_CHAT_AUTO_MESSAGE) {
    toggle(true);
    enviarMensagem(window.VOXXEL_CHAT_AUTO_MESSAGE);
  }
})();

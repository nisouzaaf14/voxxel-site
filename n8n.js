async function enviarParaN8N(mensagem) {
  try {
    const response = await fetch(
      'https://zinkoraxl.app.n8n.cloud/webhook/49fd96e7-4712-4f4e-aeb7-f4071c23dfed/chat',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          chatInput: mensagem
        })
      }
    );

    if (!response.ok) {
      throw new Error(`Erro HTTP: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Erro ao conectar com o n8n:', error);
    return null;
  }
}

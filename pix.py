# Geração de cobrança Pix (BR Code / EMV) — 100% offline, sem depender de
# gateway ou API externa. Segue o padrão do Banco Central (Pix "Copia e
# Cola" estático): o mesmo QR pode ser mostrado na tela e o cliente paga
# pelo próprio app do banco dele.
#
# Referência do formato: cada campo é ID (2 dígitos) + TAMANHO (2 dígitos)
# + VALOR. Alguns campos (26 e 62) contêm sub-campos no mesmo formato.

import io
import re
import unicodedata

import qrcode


def _campo(id_campo, valor):
    valor = str(valor)
    return f"{id_campo}{len(valor):02d}{valor}"


def _limpar_texto(texto, tamanho_max):
    """Remove acentos e caracteres fora do padrão aceito pelo Pix (o campo
    só aceita letras, números e alguns símbolos simples) e corta no tamanho."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    limpo = re.sub(r"[^A-Za-z0-9 ]", "", sem_acento).strip()
    return (limpo or "VOXXEL")[:tamanho_max]


def _crc16(payload):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) — o algoritmo exigido
    pelo Banco Central pra fechar o payload do Pix."""
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return format(crc, "04X")


def gerar_payload(chave, nome, cidade, valor, txid="VOXXEL"):
    """Monta a string "Copia e Cola" do Pix. `valor` é um número (R$);
    `txid` identifica o pedido (só letras/números, até 25 caracteres)."""
    nome = _limpar_texto(nome, 25)
    cidade = _limpar_texto(cidade, 15)
    txid_limpo = re.sub(r"[^A-Za-z0-9]", "", txid)[:25] or "VOXXEL"

    conta_pix = _campo("00", "br.gov.bcb.pix") + _campo("01", chave.strip())
    merchant_account = _campo("26", conta_pix)

    dados_adicionais = _campo("62", _campo("05", txid_limpo))

    partes = [
        _campo("00", "01"),              # Payload Format Indicator
        merchant_account,                # 26 — informações da conta Pix
        _campo("52", "0000"),            # Merchant Category Code
        _campo("53", "986"),             # moeda: Real (BRL)
        _campo("54", f"{float(valor):.2f}"),  # valor da cobrança
        _campo("58", "BR"),              # país
        _campo("59", nome),              # nome do recebedor
        _campo("60", cidade),            # cidade do recebedor
        dados_adicionais,                # 62 — txid / referência
    ]
    payload_sem_crc = "".join(partes) + "6304"
    return payload_sem_crc + _crc16(payload_sem_crc)


def gerar_qrcode_png(payload):
    """Recebe o payload do Pix e devolve os bytes do PNG do QR Code."""
    img = qrcode.make(payload, border=2, box_size=9)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()

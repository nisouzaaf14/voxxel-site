"""Regressões do núcleo que não dependem do servidor Flask.

Executar com: python -m unittest discover -s tests -v
"""
import os
import tempfile
import unittest
from pathlib import Path

import calculadora
import database
import distribuicao
import pix


class PricingAndPixTests(unittest.TestCase):
    def test_pricing_is_positive_and_finite(self):
        quote = calculadora.calcular_orcamento(5, 5, 5, 1, "tecnica", "media", "pla", "padrao")
        self.assertGreater(quote["preco_total"], 0)
        self.assertGreater(quote["liquido_parceiro"], 0)

    def test_pix_rejects_oversized_key(self):
        self.assertFalse(pix.chave_valida("x" * 100))
        with self.assertRaises(ValueError):
            pix.gerar_payload("x" * 100, "Voxxel", "Curitiba", 10)

    def test_pix_payload_has_crc(self):
        payload = pix.gerar_payload("teste@example.com", "Voxxel", "Curitiba", 12.34, "VOXXEL1")
        self.assertIn("6304", payload)
        self.assertEqual(len(payload[-4:]), 4)


class MarketplaceDatabaseTests(unittest.TestCase):
    def setUp(self):
        fd, name = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(name)
        self.path = Path(name)
        self.old_path = database.DB_PATH
        self.old_pg = database.USING_POSTGRES
        database.DB_PATH = self.path
        database.USING_POSTGRES = False
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.old_path
        database.USING_POSTGRES = self.old_pg
        if self.path.exists():
            self.path.unlink()

    def test_new_partner_starts_pending(self):
        conn = database.get_db()
        partner_id = database.criar_impressora(conn, "Parceiro", "41999999999", "hash", "pla")
        row = conn.execute("SELECT ativo, status_cadastro FROM impressoras WHERE id=?", (partner_id,)).fetchone()
        conn.close()
        self.assertEqual(row["ativo"], 0)
        self.assertEqual(row["status_cadastro"], "pendente")

    def test_cancelled_order_cannot_accept_offer(self):
        conn = database.get_db()
        partner_id = database.criar_impressora(conn, "Parceiro", "41999999999", "hash", "pla")
        database.definir_impressora_ativa(conn, partner_id, True)
        database.definir_status_impressora(conn, partner_id, True, -25.5, -49.2)
        order_id = database.criar_pedido(
            conn, "loja", "1x item", 50, "Cliente", "41988888888", "combinar",
            cliente_lat=-25.51, cliente_lng=-49.21, material_requisito="pla",
        )
        distribuicao.despachar_pedido(conn, order_id)
        offer = conn.execute(
            "SELECT * FROM ofertas_impressao WHERE pedido_id=? AND status='pendente'", (order_id,)
        ).fetchone()
        self.assertIsNotNone(offer)
        conn.execute("UPDATE pedidos SET status='cancelado', fluxo_status='cancelado' WHERE id=?", (order_id,))
        conn.commit()
        self.assertFalse(distribuicao.responder_oferta(conn, offer["id"], partner_id, True))
        order = conn.execute("SELECT impressora_id FROM pedidos WHERE id=?", (order_id,)).fetchone()
        conn.close()
        self.assertIsNone(order["impressora_id"])

    def test_only_one_pending_offer_per_order_and_partner(self):
        conn = database.get_db()
        partner_id = database.criar_impressora(conn, "Parceiro", "41999999999", "hash", "pla")
        database.definir_impressora_ativa(conn, partner_id, True)
        database.definir_status_impressora(conn, partner_id, True, -25.5, -49.2)
        order_id = database.criar_pedido(
            conn, "loja", "1x item", 50, "Cliente", "41988888888", "combinar",
            cliente_lat=-25.51, cliente_lng=-49.21, material_requisito="pla",
        )
        distribuicao.despachar_pedido(conn, order_id)
        distribuicao.avancar_distribuicao(conn, order_id)
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM ofertas_impressao WHERE pedido_id=? AND status='pendente'", (order_id,)
        ).fetchone()["n"]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

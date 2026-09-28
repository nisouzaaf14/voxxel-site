"""Guest checkout must keep the order private and never auto-confirm Pix."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database


class QuickCheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="voxxel-quick-checkout-")
        cls.old_path = database.DB_PATH
        database.DB_PATH = Path(cls.tmp.name) / "orders.db"
        import app as site
        cls.site = site
        cls.old_ids = site.LIVE_PRODUCT_IDS
        database.init_db()
        conn = database.get_db()
        conn.execute("UPDATE produtos SET ativo=1 WHERE id=1")
        conn.commit()
        database.set_configs(conn, {"pix_chave": "teste@example.com", "pix_nome": "VOXXEL TESTE", "pix_cidade": "Curitiba"})
        conn.close()
        site.LIVE_PRODUCT_IDS = {1}

    @classmethod
    def tearDownClass(cls):
        cls.site.LIVE_PRODUCT_IDS = cls.old_ids
        database.DB_PATH = cls.old_path
        cls.tmp.cleanup()

    def setUp(self):
        self.client = self.site.app.test_client()
        self.client.get("/ao-vivo")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf_token"]

    def buy(self, receiving="retirada", payment="pix", address=""):
        response = self.client.post("/ao-vivo/comprar/1", data={"csrf_token": self.csrf})
        self.assertEqual(response.location, "/checkout")
        with patch.object(self.site.distribuicao, "despachar_pedido"):
            return self.client.post("/checkout", data={
                "csrf_token": self.csrf, "nome": "Cliente Teste", "telefone": "41999999999",
                "email": "teste@example.com", "recebimento": receiving,
                "forma_pagamento": payment, "endereco": address,
            }, follow_redirects=True)

    def test_guest_can_pay_pix_and_reopen_with_private_link(self):
        response = self.buy()
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="pix-copiaecola"', response.text)
        conn = database.get_db()
        order = conn.execute("SELECT * FROM pedidos ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(order["forma_pagamento"], "pix")
        self.assertNotEqual(order["status_pagamento"], "confirmado")
        self.assertEqual(order["lead_origem"], "ao_vivo")
        conn.close()
        self.assertEqual(self.site.app.test_client().get(f"/pedido/{order['id']}/pagamento").status_code, 403)
        with self.client.session_transaction() as session:
            token = session["links_pedidos"][str(order["id"])]
        self.assertIn('id="pix-copiaecola"', self.site.app.test_client().get("/acompanhar/" + token, follow_redirects=True).text)

    def test_delivery_cannot_charge_subtotal_without_freight(self):
        response = self.buy("entrega", "pix", "Rua de Teste, 123, Curitiba, PR, 80000-000")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="pix-copiaecola"', response.text)
        conn = database.get_db()
        order = conn.execute("SELECT forma_pagamento FROM pedidos ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(order["forma_pagamento"], "combinar")
        conn.close()


if __name__ == "__main__":
    unittest.main()

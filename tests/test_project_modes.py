"""Isolated route regressions; no production DB or external dispatch."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from PIL import Image


class ProjectModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="voxxel-mode-tests-")
        cls.old_path = database.DB_PATH
        cls.old_pg = database.USING_POSTGRES
        database.DB_PATH = Path(cls.temp.name) / "test.db"
        database.USING_POSTGRES = False
        import app
        cls.module = app
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        database.DB_PATH = cls.old_path
        database.USING_POSTGRES = cls.old_pg
        cls.temp.cleanup()

    def post(self, route, data):
        self.client.get(route)
        with self.client.session_transaction() as session:
            data["csrf_token"] = session["csrf_token"]
        with patch.object(self.module.distribuicao, "despachar_pedido"):
            return self.client.post(route, data=data, content_type="multipart/form-data")

    def test_modes_render_and_technical_resources_remain(self):
        for route in ["/projetos", "/ideias", "/simplificado", "/orcamento", "/empresas", "/loja"]:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, route)
        html = self.client.get("/orcamento").text
        for resource in ["quote-visualizer", "material-input", "qualidade-input", "complexidade-input", "breakdown-toggle", "brief-changes"]:
            self.assertIn(resource, html)
        self.assertNotIn('<details class="quote-advanced-panel">', html)
        selector = self.client.get("/projetos").text
        self.assertIn('href="/ideias"', selector)
        self.assertIn('href="/orcamento"', selector)
        self.assertIn('href="/empresas"', selector)
        self.assertEqual(selector.count('class="project-option '), 3)
        home = self.client.get("/").text
        self.assertIn('href="/projetos">Começar meu projeto', home)
        self.assertNotIn("Pedir uma análise", home)
        self.assertNotIn("Encontrar meu caminho", selector)
        company = self.client.get("/empresas").text
        self.assertGreaterEqual(company.count("Solicitar orçamento empresarial"), 3)

    def test_business_contact_without_attachment(self):
        response = self.post("/empresas", {
            "acao": "enviar", "origem": "empresas", "nome": "Teste local",
            "telefone": "41999999999", "email": "qa@example.com",
            "empresa_nome": "Empresa de teste", "cnpj": "Informado em teste",
            "prazo": "30 dias", "demanda": "recorrente",
            "quantidade_aproximada": "500 por mês",
            "descricao_projeto": "Caixas para um protótipo eletrônico.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("/acompanhar/", response.text)
        conn = database.get_db()
        row = conn.execute("SELECT requisitos_projeto FROM pedidos ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("500 por mês", row["requisitos_projeto"])
        self.assertIn("recorrente", row["requisitos_projeto"])
        conn.close()

    def test_guided_upload_without_technical_answers(self):
        image = io.BytesIO()
        Image.new("RGB", (32, 32), "#9d6bce").save(image, format="PNG")
        image.seek(0)
        response = self.post("/ideias", {
            "acao": "enviar", "origem": "orcamento", "nome": "Teste guiado",
            "telefone": "41999999999", "email": "qa@example.com",
            "descricao_projeto": "Preciso substituir a tampa de uma caixa.",
            "quantidade": "2", "imagens_referencia": (image, "referencia.png"),
        })
        self.assertIn("/acompanhar/", response.text)

    def test_validation_stays_in_its_mode(self):
        response = self.post("/empresas", {"acao":"enviar", "origem":"empresas", "nome":"Teste"})
        self.assertIn('name="cnpj"', response.text)
        response = self.post("/simplificado", {"acao":"enviar", "descricao_projeto":"Curto"})
        self.assertIn("guided-project-form", response.text)

    def test_csrf_and_catalog_images(self):
        self.assertEqual(self.client.post("/simplificado", data={"acao":"enviar"}).status_code, 303)
        conn = database.get_db()
        rows = conn.execute("SELECT id,nome,imagem_arquivo,imagem_tipo FROM produtos").fetchall()
        self.assertEqual(len(rows), 18)
        for row in rows:
            self.assertEqual(row["imagem_tipo"], "ilustrativa", row["nome"])
            self.assertTrue(row["imagem_arquivo"], row["nome"])
            image = self.client.get("/produto/" + str(row["id"]) + "/imagem")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.mimetype, "image/webp")
            image.close()
            self.assertEqual(self.client.get("/produto/" + str(row["id"])).status_code, 200)
        conn.close()

    def test_cart_and_checkout_render(self):
        self.client.get("/loja")
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        response = self.client.post("/carrinho/adicionar/1", data={"csrf_token":token,"quantidade":"2"})
        self.assertIn(response.status_code, (302,303))
        self.assertEqual(self.client.get("/carrinho").status_code, 200)
        self.assertEqual(self.client.get("/checkout", follow_redirects=True).status_code, 200)


if __name__ == "__main__":
    unittest.main()

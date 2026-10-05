import sys
import unittest
from decimal import Decimal
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from tropa_pdf import calcular_depuracion, extraer_registros  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "seleccion_previa_sintetica.pdf"


class PruebasPDFSintetico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registros, cls.diagnostico = extraer_registros(FIXTURE)
        cls.depuracion = calcular_depuracion(cls.registros)

    def test_extrae_todas_las_filas(self):
        self.assertEqual(len(self.registros), 5)
        self.assertEqual(self.diagnostico.paginas_pdf, 1)
        self.assertEqual(self.diagnostico.paginas_datos, 1)

    def test_adjudicacion_respeta_preferencias_y_plazas(self):
        self.assertEqual(
            self.depuracion.asignacion["99999-00-00001"], ("60000", 1)
        )
        self.assertEqual(
            self.depuracion.asignacion["99999-00-00003"], ("60001", 1)
        )
        self.assertNotIn("99999-00-00002", self.depuracion.asignacion)

    def test_posiciones_y_cortes_depurados(self):
        self.assertEqual(
            self.depuracion.posicion[("99999-00-00001", "60000")], 1
        )
        self.assertEqual(
            self.depuracion.posicion[("99999-00-00001", "60001")], 1
        )
        self.assertNotIn(
            ("99999-00-00001", "60001"), self.depuracion.en_lista
        )
        self.assertEqual(
            self.depuracion.posicion[("99999-00-00002", "60001")], 2
        )
        self.assertEqual(self.depuracion.corte_por_codigo["60000"], Decimal("9.500"))
        self.assertEqual(self.depuracion.corte_por_codigo["60001"], Decimal("9.000"))


if __name__ == "__main__":
    unittest.main()

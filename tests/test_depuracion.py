import sys
import unittest
from collections import Counter, defaultdict
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

    def test_volumen_y_paginacion(self):
        self.assertEqual(len(self.registros), 10_000)
        self.assertEqual(self.diagnostico.paginas_pdf, 150)
        self.assertEqual(self.diagnostico.paginas_datos, 150)
        self.assertEqual(self.diagnostico.paginas_recuperadas_por_coordenadas, ())
        self.assertEqual(len({fila.nio for fila in self.registros}), 1_000)
        self.assertEqual(len({fila.vacante for fila in self.registros}), 50)

    def test_distribucion_equilibrada_y_limite_de_preferencias(self):
        por_codigo = Counter(fila.vacante for fila in self.registros)
        por_persona = defaultdict(list)
        for fila in self.registros:
            por_persona[fila.nio].append(fila)

        self.assertEqual(set(por_codigo.values()), {200})
        self.assertEqual({fila.plazas for fila in self.registros}, {10})
        self.assertEqual(set(len(filas) for filas in por_persona.values()), {10})
        for filas in por_persona.values():
            self.assertEqual(
                sorted(fila.orden_peticion for fila in filas), list(range(1, 11))
            )
            self.assertEqual(len({fila.vacante for fila in filas}), 10)

    def test_adjudicacion_entrelazada_cubre_las_500_plazas(self):
        asignados_por_codigo = Counter(
            codigo for codigo, _ in self.depuracion.asignacion.values()
        )
        preferencias_asignadas = Counter(
            preferencia for _, preferencia in self.depuracion.asignacion.values()
        )

        self.assertEqual(len(self.depuracion.asignacion), 500)
        self.assertEqual(set(asignados_por_codigo.values()), {10})
        self.assertEqual(set(preferencias_asignadas), set(range(1, 11)))

    def test_posiciones_y_cortes_depurados_son_coherentes(self):
        self.assertTrue(
            all(corte is not None for corte in self.depuracion.corte_por_codigo.values())
        )
        self.assertTrue(
            all(
                self.depuracion.posicion[(nio, codigo)] <= 10
                for nio, (codigo, _) in self.depuracion.asignacion.items()
            )
        )
        self.assertTrue(
            any(
                (fila.nio, fila.vacante) not in self.depuracion.en_lista
                for fila in self.registros
            )
        )


if __name__ == "__main__":
    unittest.main()

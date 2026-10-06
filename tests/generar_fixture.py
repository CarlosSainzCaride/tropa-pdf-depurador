"""Genera un PDF sintético de carga para las pruebas automatizadas."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


DESTINO = Path(__file__).parent / "fixtures" / "seleccion_previa_sintetica.pdf"

NUM_ASPIRANTES = 1_000
NUM_ESPECIALIDADES = 50
PLAZAS_POR_ESPECIALIDAD = 10
PREFERENCIAS_POR_ASPIRANTE = 10
FILAS_POR_PAGINA = 67
CODIGO_INICIAL = 60_000


@dataclass(frozen=True, slots=True)
class FilaSintetica:
    codigo: str
    plazas: int
    nota_milesimas: int
    preferencia: int
    puntos_centesimas: int
    fecha_nacimiento: date
    nio: str


def _preferencias(indice: int) -> list[int]:
    """Devuelve diez códigos distintos y perfectamente equilibrados."""
    bloque, posicion = divmod(indice, NUM_ESPECIALIDADES)
    base = (posicion * 17 + bloque * 3) % NUM_ESPECIALIDADES
    return [
        (base + desplazamiento * 7) % NUM_ESPECIALIDADES
        for desplazamiento in range(PREFERENCIAS_POR_ASPIRANTE)
    ]


def construir_filas() -> list[str]:
    por_codigo: dict[str, list[FilaSintetica]] = defaultdict(list)

    for indice in range(NUM_ASPIRANTES):
        nio = f"99999-00-{indice + 1:05d}"
        nacimiento = date(
            1988 + indice % 17,
            1 + (indice // 28) % 12,
            1 + indice % 28,
        )

        for preferencia, indice_codigo in enumerate(_preferencias(indice), start=1):
            codigo = f"{CODIGO_INICIAL + indice_codigo:05d}"
            # La nota cambia entre especialidades para que los rankings no sean
            # una simple copia global y la adjudicación tenga rechazos en cascada.
            nota = 4_500 + (
                indice * 7_919 + indice_codigo * 1_543 + preferencia * 337
            ) % 5_200
            puntos = 300 + (indice * 97 + indice_codigo * 53) % 1_700
            por_codigo[codigo].append(
                FilaSintetica(
                    codigo=codigo,
                    plazas=PLAZAS_POR_ESPECIALIDAD,
                    nota_milesimas=nota,
                    preferencia=preferencia,
                    puntos_centesimas=puntos,
                    fecha_nacimiento=nacimiento,
                    nio=nio,
                )
            )

    salida: list[str] = []
    for codigo in sorted(por_codigo):
        ordenadas = sorted(
            por_codigo[codigo],
            key=lambda fila: (
                -fila.nota_milesimas,
                -fila.puntos_centesimas,
                fila.fecha_nacimiento,
                fila.nio,
            ),
        )
        for orden_parcial, fila in enumerate(ordenadas, start=1):
            nota = f"{fila.nota_milesimas // 1000},{fila.nota_milesimas % 1000:03d}"
            puntos = f"{fila.puntos_centesimas // 100},{fila.puntos_centesimas % 100:02d}"
            salida.append(
                f"{fila.codigo} {fila.plazas} {nota} {fila.preferencia} "
                f"{puntos} {fila.fecha_nacimiento:%d-%m-%Y} "
                f"{orden_parcial} {fila.nio}"
            )

    return salida


def _cabecera_pagina(pdf: canvas.Canvas, pagina: int, paginas: int) -> None:
    ancho, alto = A4
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(36, alto - 34, "SELECCIÓN PREVIA - MUESTRA SINTÉTICA DE CARGA")
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(
        36,
        alto - 48,
        "1.000 aspirantes | 50 especialidades | 10 plazas por especialidad | "
        "10 preferencias por aspirante",
    )
    pdf.drawRightString(ancho - 36, alto - 34, f"Página {pagina} de {paginas}")

    pdf.setFont("Courier-Bold", 7.2)
    pdf.drawString(
        36,
        alto - 68,
        "Vacante Plazas Nota Final Ord.Petic. Ptos.Conc. Fecha Nac. Ord.Parc. N.I.O.",
    )
    pdf.line(36, alto - 72, ancho - 36, alto - 72)

    pdf.setFont("Helvetica-Oblique", 6.5)
    pdf.drawString(
        36,
        25,
        "Datos totalmente ficticios. Este documento no pertenece a ninguna convocatoria real.",
    )


def generar(destino: Path = DESTINO) -> Path:
    filas = construir_filas()
    esperado = NUM_ASPIRANTES * PREFERENCIAS_POR_ASPIRANTE
    if len(filas) != esperado:
        raise RuntimeError(f"Se esperaban {esperado} filas y se generaron {len(filas)}")

    destino.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(
        str(destino),
        pagesize=A4,
        pageCompression=1,
        invariant=1,
    )
    pdf.setTitle("Selección previa sintética de carga")
    pdf.setAuthor("Contributors")
    pdf.setSubject("Fixture de 1.000 aspirantes y 50 especialidades con datos ficticios")

    paginas = math.ceil(len(filas) / FILAS_POR_PAGINA)
    alto = A4[1]
    codigo_anterior: str | None = None

    for indice, fila in enumerate(filas):
        posicion_pagina = indice % FILAS_POR_PAGINA
        if posicion_pagina == 0:
            if indice:
                pdf.showPage()
            _cabecera_pagina(pdf, indice // FILAS_POR_PAGINA + 1, paginas)

        y = alto - 87 - posicion_pagina * 10.75
        codigo = fila[:5]
        if codigo != codigo_anterior:
            pdf.setFillColor(Color(0.91, 0.94, 0.98))
            pdf.rect(34, y - 2.2, A4[0] - 68, 9.2, stroke=0, fill=1)
            pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Courier", 7.2)
        pdf.drawString(36, y, fila)
        codigo_anterior = codigo

    pdf.save()
    return destino


if __name__ == "__main__":
    print(generar())

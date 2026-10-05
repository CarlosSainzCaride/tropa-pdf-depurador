"""Genera el PDF sintético usado por las pruebas automatizadas."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


DESTINO = Path(__file__).parent / "fixtures" / "seleccion_previa_sintetica.pdf"

FILAS = (
    "60000 1 9,500 1 10,00 01-01-2000 1 99999-00-00001",
    "60000 1 8,000 1  8,00 02-01-2000 2 99999-00-00002",
    "60001 1 9,500 2 10,00 01-01-2000 1 99999-00-00001",
    "60001 1 9,000 1  9,00 03-01-2000 2 99999-00-00003",
    "60001 1 8,000 2  8,00 02-01-2000 3 99999-00-00002",
)


def generar(destino: Path = DESTINO) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(destino), pagesize=A4, pageCompression=0)
    pdf.setTitle("Selección previa sintética")
    pdf.setAuthor("Contributors")
    pdf.setSubject("Fixture con datos ficticios para pruebas automatizadas")

    ancho, alto = A4
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(42, alto - 48, "SELECCIÓN PREVIA - MUESTRA SINTÉTICA")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(42, alto - 64, "Todos los identificadores y datos de esta página son ficticios.")

    pdf.setFont("Courier-Bold", 8)
    pdf.drawString(
        42,
        alto - 92,
        "Vacante Plazas Nota Final Ord.Petic. Ptos.Conc. Fecha Nac. Ord.Parc. N.I.O.",
    )
    pdf.line(42, alto - 97, ancho - 42, alto - 97)

    pdf.setFont("Courier", 9)
    y = alto - 116
    for fila in FILAS:
        pdf.drawString(42, y, fila)
        y -= 18

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(42, 42, "Fixture público: no procede de ninguna convocatoria real.")
    pdf.save()
    return destino


if __name__ == "__main__":
    print(generar())

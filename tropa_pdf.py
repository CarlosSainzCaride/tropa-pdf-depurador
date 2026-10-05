#!/usr/bin/env python3
"""Extrae y ordena la seleccion previa de Tropa y Marineria desde PDF.

El programa esta pensado para los PDF oficiales con las columnas:
Vacante, Plazas, Nota Final, Ord.Petic., Ptos.Conc., Fecha Nac.,
Ord.Parc. y N.I.O.

La lectura principal usa pypdf. Si alguna fila queda ambigua, la pagina se
vuelve a leer por coordenadas con pdfplumber. Nunca se omiten filas de forma
silenciosa: las invariantes del documento se validan antes de mostrar o
exportar resultados.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Sequence


VERSION = "2.0.0"

ROW_RE = re.compile(
    r"(?m)^\s*"
    r"(?P<vacante>\d{5})[^\S\r\n]+"
    r"(?P<plazas>\d+)[^\S\r\n]+"
    r"(?P<nota>\d+(?:,\d{1,3})?)[^\S\r\n]+"
    r"(?P<orden_peticion>\d+)[^\S\r\n]+"
    r"(?P<puntos_concurso>\d+(?:,\d{1,2})?)[^\S\r\n]+"
    r"(?P<fecha_nacimiento>\d{2}-\d{2}-\d{4})[^\S\r\n]+"
    r"(?P<orden_parcial>\d+)[^\S\r\n]+"
    r"(?P<nio>\d{5}-\d{2}-\d{5})\s*$"
)
NIO_RE = re.compile(r"\b\d{5}-\d{2}-\d{5}\b")
CODE_RE = re.compile(r"^\d{5}$")
ROW_CANDIDATE_RE = re.compile(r"(?m)^[^\S\r\n]*\d{5}[^\S\r\n]+[^\r\n]+$")

# Centros relativos de las ocho columnas del formato oficial. Solo se usan
# como recuperacion cuando la capa de texto concatena celdas independientes.
COORD_COLUMN_CENTERS = (0.136, 0.235, 0.333, 0.432, 0.532, 0.630, 0.729, 0.845)


class ExtractionError(RuntimeError):
    """El PDF no se pudo interpretar de manera completa y verificable."""


@dataclass(frozen=True, slots=True)
class Registro:
    vacante: str
    plazas: int
    nota_final: Decimal
    orden_peticion: int
    puntos_concurso: Decimal
    fecha_nacimiento: date
    orden_parcial: int
    nio: str
    pagina_pdf: int

    @property
    def dentro_cupo_bruto(self) -> bool:
        return self.orden_parcial <= self.plazas

    @property
    def margen_cupo_bruto(self) -> int:
        return self.plazas - self.orden_parcial


@dataclass(frozen=True, slots=True)
class Diagnostico:
    paginas_pdf: int
    paginas_datos: int
    paginas_recuperadas_por_coordenadas: tuple[int, ...]
    hash_sha256: str


@dataclass(frozen=True, slots=True)
class Depuracion:
    """Resultado global de resolver plazas y preferencias hasta estabilizarse."""

    asignacion: dict[str, tuple[str, int]]
    posicion: dict[tuple[str, str], int]
    en_lista: frozenset[tuple[str, str]]
    corte_por_codigo: dict[str, Decimal | None]
    aspirantes_por_codigo: dict[str, int]


def decimal_es(texto: str) -> Decimal:
    normalizado = texto.strip().replace(" ", "")
    # El PDF usa coma decimal. En la linea de comandos aceptamos tambien punto
    # decimal para que tanto ``5,500`` como ``5.500`` funcionen en Windows.
    if "," in normalizado:
        normalizado = normalizado.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalizado)
    except InvalidOperation as exc:
        raise ValueError(f"Numero no valido: {texto!r}") from exc


def texto_decimal_es(valor: Decimal) -> str:
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def fecha_es(texto: str) -> date:
    try:
        return datetime.strptime(texto.strip(), "%d-%m-%Y").date()
    except ValueError as exc:
        raise ValueError(f"Fecha no valida: {texto!r}") from exc


def sha256_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as flujo:
        for bloque in iter(lambda: flujo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest().upper()


def registro_desde_campos(campos: Sequence[str], pagina: int) -> Registro:
    if len(campos) != 8:
        raise ValueError(f"Se esperaban 8 columnas y llegaron {len(campos)}")

    vacante, plazas, nota, preferencia, puntos, fecha, orden, nio = campos
    registro = Registro(
        vacante=vacante.strip(),
        plazas=int(plazas),
        nota_final=decimal_es(nota),
        orden_peticion=int(preferencia),
        puntos_concurso=decimal_es(puntos),
        fecha_nacimiento=fecha_es(fecha),
        orden_parcial=int(orden),
        nio=nio.strip(),
        pagina_pdf=pagina,
    )

    if not CODE_RE.fullmatch(registro.vacante):
        raise ValueError(f"Codigo de vacante no valido: {registro.vacante!r}")
    if not NIO_RE.fullmatch(registro.nio):
        raise ValueError(f"NIO no valido: {registro.nio!r}")
    if registro.plazas < 1:
        raise ValueError("El numero de plazas debe ser positivo")
    if not 1 <= registro.orden_peticion <= 10:
        raise ValueError("El orden de peticion debe estar entre 1 y 10")
    if registro.orden_parcial < 1:
        raise ValueError("El orden parcial debe ser positivo")
    if not Decimal("0") <= registro.nota_final <= Decimal("10"):
        raise ValueError("La nota final debe estar entre 0 y 10")
    return registro


def registro_desde_match(match: re.Match[str], pagina: int) -> Registro:
    nombres = (
        "vacante",
        "plazas",
        "nota",
        "orden_peticion",
        "puntos_concurso",
        "fecha_nacimiento",
        "orden_parcial",
        "nio",
    )
    return registro_desde_campos([match.group(nombre) for nombre in nombres], pagina)


def _agrupar_palabras_por_linea(palabras: list[dict], tolerancia: float = 1.8) -> list[list[dict]]:
    lineas: list[list[dict]] = []
    for palabra in sorted(palabras, key=lambda w: (float(w["top"]), float(w["x0"]))):
        if not lineas or abs(float(palabra["top"]) - float(lineas[-1][0]["top"])) > tolerancia:
            lineas.append([palabra])
        else:
            lineas[-1].append(palabra)
    return lineas


def _extraer_pagina_por_coordenadas(pagina_pdfplumber, numero_pagina: int) -> list[Registro]:
    """Recupera filas usando posiciones X cuando pypdf pega varias celdas."""
    palabras = pagina_pdfplumber.extract_words(
        x_tolerance=1.5,
        y_tolerance=1.5,
        keep_blank_chars=False,
        use_text_flow=False,
    )
    ancho = float(pagina_pdfplumber.width)
    centros = [ancho * proporcion for proporcion in COORD_COLUMN_CENTERS]
    registros: list[Registro] = []

    for linea in _agrupar_palabras_por_linea(palabras):
        celdas: list[list[tuple[float, str]]] = [[] for _ in range(8)]
        for palabra in linea:
            centro_x = (float(palabra["x0"]) + float(palabra["x1"])) / 2
            indice = min(range(8), key=lambda i: abs(centro_x - centros[i]))
            celdas[indice].append((float(palabra["x0"]), str(palabra["text"])))

        campos = ["".join(texto for _, texto in sorted(celda)) for celda in celdas]
        if not NIO_RE.fullmatch(campos[7]):
            continue
        try:
            registros.append(registro_desde_campos(campos, numero_pagina))
        except (TypeError, ValueError) as exc:
            raise ExtractionError(
                f"No se pudo reconstruir una fila de la pagina {numero_pagina}: "
                f"{campos!r}. Motivo: {exc}"
            ) from exc
    return registros


def extraer_registros(ruta_pdf: Path) -> tuple[list[Registro], Diagnostico]:
    if not ruta_pdf.is_file():
        raise ExtractionError(f"No existe el PDF: {ruta_pdf}")

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError(
            "Falta pypdf. Instala las dependencias con: "
            "py -m pip install -r requirements.txt"
        ) from exc

    try:
        lector = PdfReader(str(ruta_pdf), strict=False)
        if lector.is_encrypted:
            resultado = lector.decrypt("")
            if not resultado:
                raise ExtractionError("El PDF esta protegido con contrasena")
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"No se pudo abrir el PDF: {exc}") from exc

    registros_por_pagina: dict[int, list[Registro]] = {}
    paginas_ambiguas: list[int] = []
    paginas_con_datos = 0

    for numero, pagina in enumerate(lector.pages, start=1):
        try:
            texto = (pagina.extract_text() or "").replace("\u00a0", " ")
        except Exception as exc:
            raise ExtractionError(f"Fallo al leer el texto de la pagina {numero}: {exc}") from exc

        coincidencias = list(ROW_RE.finditer(texto))
        nios_en_texto = NIO_RE.findall(texto)
        lineas_candidatas = ROW_CANDIDATE_RE.findall(texto)
        if nios_en_texto or lineas_candidatas:
            paginas_con_datos += 1
        if len(coincidencias) != len(nios_en_texto) or len(coincidencias) != len(
            lineas_candidatas
        ):
            paginas_ambiguas.append(numero)
        registros_por_pagina[numero] = [
            registro_desde_match(coincidencia, numero) for coincidencia in coincidencias
        ]

    if not any(registros_por_pagina.values()):
        raise ExtractionError(
            "No se encontraron filas. El PDF puede ser una imagen escaneada o usar otro formato."
        )

    if paginas_ambiguas:
        try:
            import pdfplumber
        except ImportError as exc:
            paginas = ", ".join(map(str, paginas_ambiguas))
            raise ExtractionError(
                "Hay filas ambiguas en las paginas "
                f"{paginas} y falta pdfplumber para recuperarlas. "
                "Instala las dependencias con: py -m pip install -r requirements.txt"
            ) from exc

        try:
            with pdfplumber.open(str(ruta_pdf)) as pdf:
                for numero in paginas_ambiguas:
                    recuperados = _extraer_pagina_por_coordenadas(pdf.pages[numero - 1], numero)
                    texto = lector.pages[numero - 1].extract_text() or ""
                    esperados = max(
                        len(NIO_RE.findall(texto)), len(ROW_CANDIDATE_RE.findall(texto))
                    )
                    if len(recuperados) != esperados:
                        raise ExtractionError(
                            f"La pagina {numero} contiene {esperados} NIO, pero solo se "
                            f"pudieron reconstruir {len(recuperados)} filas."
                        )
                    registros_por_pagina[numero] = recuperados
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(f"Fallo en la recuperacion por coordenadas: {exc}") from exc

    registros = [
        registro
        for numero in sorted(registros_por_pagina)
        for registro in registros_por_pagina[numero]
    ]
    validar_registros(registros)
    diagnostico = Diagnostico(
        paginas_pdf=len(lector.pages),
        paginas_datos=paginas_con_datos,
        paginas_recuperadas_por_coordenadas=tuple(paginas_ambiguas),
        hash_sha256=sha256_archivo(ruta_pdf),
    )
    return registros, diagnostico


def validar_registros(registros: Sequence[Registro]) -> None:
    """Comprueba integridad global para impedir exportaciones incompletas."""
    if not registros:
        raise ExtractionError("La extraccion no produjo registros")

    vistos: set[tuple[str, str]] = set()
    duplicados: list[tuple[str, str]] = []
    por_codigo: dict[str, list[Registro]] = defaultdict(list)
    por_persona: dict[str, list[Registro]] = defaultdict(list)

    for registro in registros:
        clave = (registro.nio, registro.vacante)
        if clave in vistos:
            duplicados.append(clave)
        vistos.add(clave)
        por_codigo[registro.vacante].append(registro)
        por_persona[registro.nio].append(registro)

    if duplicados:
        muestra = ", ".join(f"{nio}/{codigo}" for nio, codigo in duplicados[:5])
        raise ExtractionError(f"Se detectaron filas duplicadas NIO/vacante: {muestra}")

    errores: list[str] = []
    for codigo, filas in por_codigo.items():
        plazas = {fila.plazas for fila in filas}
        if len(plazas) != 1:
            errores.append(f"{codigo}: valores de plazas distintos")

        ordenadas = sorted(filas, key=lambda fila: fila.orden_parcial)
        ordenes = [fila.orden_parcial for fila in ordenadas]
        esperado = list(range(1, len(ordenadas) + 1))
        if ordenes != esperado:
            errores.append(f"{codigo}: Ord.Parc. no es consecutivo 1..{len(ordenadas)}")

        if any(
            anterior.nota_final < siguiente.nota_final
            for anterior, siguiente in zip(ordenadas, ordenadas[1:])
        ):
            errores.append(f"{codigo}: las notas no estan en orden no creciente")

    for nio, filas in por_persona.items():
        preferencias = sorted(fila.orden_peticion for fila in filas)
        esperado = list(range(1, len(filas) + 1))
        if preferencias != esperado:
            errores.append(f"{nio}: preferencias no consecutivas 1..{len(filas)}")
        if len({fila.fecha_nacimiento for fila in filas}) != 1:
            errores.append(f"{nio}: fecha de nacimiento inconsistente")

    if errores:
        detalle = "\n  - ".join(errores[:20])
        resto = "" if len(errores) <= 20 else f"\n  ... y {len(errores) - 20} errores mas"
        raise ExtractionError(f"La validacion del PDF ha fallado:\n  - {detalle}{resto}")


def calcular_depuracion(registros: Sequence[Registro]) -> Depuracion:
    """Resuelve simultaneamente plazas y preferencias mediante adjudicacion diferida.

    Cada aspirante solicita sus vacantes por orden de peticion. Cada vacante
    conserva provisionalmente a los mejores segun el ``Ord.Parc.`` oficial y
    rechaza al resto, que pasa a su siguiente opcion. El proceso termina cuando
    ya no hay rechazos. Despues se eliminan de cada lista las filas de quienes
    obtuvieron una preferencia anterior.

    Para una fila eliminada se conserva una posicion contrafactual: el puesto
    que ocuparia la persona si perdiera su asignacion anterior. Esto permite
    mostrar correctamente casos como una segunda preferencia en azul.
    """

    por_persona: dict[str, list[Registro]] = defaultdict(list)
    por_codigo: dict[str, list[Registro]] = defaultdict(list)
    plazas: dict[str, int] = {}
    ranking: dict[tuple[str, str], int] = {}

    for fila in registros:
        por_persona[fila.nio].append(fila)
        por_codigo[fila.vacante].append(fila)
        plazas[fila.vacante] = fila.plazas
        ranking[(fila.vacante, fila.nio)] = fila.orden_parcial

    for filas in por_persona.values():
        filas.sort(key=lambda fila: fila.orden_peticion)
    for filas in por_codigo.values():
        filas.sort(key=lambda fila: fila.orden_parcial)

    siguiente = {nio: 0 for nio in por_persona}
    retenidos: dict[str, list[tuple[int, str]]] = defaultdict(list)
    pendientes = deque(por_persona)

    while pendientes:
        nio = pendientes.popleft()
        indice = siguiente[nio]
        if indice >= len(por_persona[nio]):
            continue

        fila = por_persona[nio][indice]
        siguiente[nio] += 1
        heap = retenidos[fila.vacante]
        # El menor Ord.Parc. es mejor. Se usa signo negativo para expulsar
        # primero el peor puesto de la cola limitada por las plazas.
        heapq.heappush(heap, (-ranking[(fila.vacante, nio)], nio))
        if len(heap) > plazas[fila.vacante]:
            _, rechazado = heapq.heappop(heap)
            pendientes.append(rechazado)

    asignacion: dict[str, tuple[str, int]] = {}
    for codigo, heap in retenidos.items():
        for _, nio in heap:
            fila = next(f for f in por_persona[nio] if f.vacante == codigo)
            asignacion[nio] = (codigo, fila.orden_peticion)

    posicion: dict[tuple[str, str], int] = {}
    en_lista: set[tuple[str, str]] = set()
    corte_por_codigo: dict[str, Decimal | None] = {}
    aspirantes_por_codigo: dict[str, int] = {}

    for codigo, filas in por_codigo.items():
        contador = 0
        corte: Decimal | None = None
        for fila in filas:
            asignada = asignacion.get(fila.nio)
            tiene_preferencia_anterior = (
                asignada is not None and asignada[1] < fila.orden_peticion
            )
            clave = (fila.nio, fila.vacante)
            if tiene_preferencia_anterior:
                # Puesto hipotetico si desapareciera la asignacion anterior.
                posicion[clave] = contador + 1
            else:
                contador += 1
                posicion[clave] = contador
                en_lista.add(clave)
                if contador == fila.plazas:
                    corte = fila.nota_final
        aspirantes_por_codigo[codigo] = contador
        corte_por_codigo[codigo] = corte

    errores: list[str] = []
    for nio, (codigo, _) in asignacion.items():
        fila = next(f for f in por_persona[nio] if f.vacante == codigo)
        if posicion[(nio, codigo)] > fila.plazas:
            errores.append(
                f"{nio}/{codigo}: asignado en puesto depurado "
                f"{posicion[(nio, codigo)]}>{fila.plazas}"
            )
    if errores:
        detalle = "\n  - ".join(errores[:20])
        raise ExtractionError(f"La depuracion ha producido asignaciones invalidas:\n  - {detalle}")

    return Depuracion(
        asignacion=asignacion,
        posicion=posicion,
        en_lista=frozenset(en_lista),
        corte_por_codigo=corte_por_codigo,
        aspirantes_por_codigo=aspirantes_por_codigo,
    )


def estado_depurado(registro: Registro, depuracion: Depuracion) -> str:
    asignada = depuracion.asignacion.get(registro.nio)
    posicion = depuracion.posicion[(registro.nio, registro.vacante)]
    if asignada is not None and asignada[0] == registro.vacante:
        return "TITULAR"
    if asignada is not None and asignada[1] < registro.orden_peticion:
        if posicion <= registro.plazas:
            return "TITULAR EN PREFERENCIA ANTERIOR"
        return "ASIGNADO EN PREFERENCIA ANTERIOR"
    return "RESERVA"


def _codigos_argumento(grupos: list[list[str]] | None) -> set[str]:
    codigos = {codigo.strip() for grupo in (grupos or []) for codigo in grupo}
    invalidos = sorted(codigo for codigo in codigos if not CODE_RE.fullmatch(codigo))
    if invalidos:
        raise ValueError(f"Codigos no validos: {', '.join(invalidos)}")
    return codigos


def filtrar_registros(
    registros: Sequence[Registro], args: argparse.Namespace, depuracion: Depuracion
) -> list[Registro]:
    codigos = _codigos_argumento(args.codigo)
    resultado = list(registros)

    if codigos:
        resultado = [fila for fila in resultado if fila.vacante in codigos]

    if args.persona:
        consulta = re.sub(r"\s+", "", args.persona.upper())
        nios = {fila.nio for fila in registros if consulta in fila.nio.upper()}
        resultado = [fila for fila in resultado if fila.nio in nios]

    if args.preferencia is not None:
        resultado = [fila for fila in resultado if fila.orden_peticion == args.preferencia]
    if args.fecha_nacimiento is not None:
        resultado = [fila for fila in resultado if fila.fecha_nacimiento == args.fecha_nacimiento]
    if args.nota is not None:
        resultado = [fila for fila in resultado if fila.nota_final == args.nota]
    if args.solo_cupo_bruto:
        resultado = [fila for fila in resultado if fila.dentro_cupo_bruto]
    if args.solo_cupo_depurado:
        resultado = [
            fila
            for fila in resultado
            if depuracion.posicion[(fila.nio, fila.vacante)] <= fila.plazas
        ]

    # Una consulta por codigo representa por defecto la lista depurada real:
    # se retiran quienes ya obtienen una preferencia anterior. En una consulta
    # personal se conservan todas las preferencias y se muestra su posicion
    # contrafactual, como hacen las listas depuradas personales.
    if codigos and not args.persona and not args.incluir_excluidos:
        resultado = [
            fila for fila in resultado if (fila.nio, fila.vacante) in depuracion.en_lista
        ]

    orden = args.orden
    if orden == "automatico":
        orden = "persona" if args.persona else ("depurada" if codigos else "vacante")
    if orden == "persona":
        resultado.sort(key=lambda fila: (fila.nio, fila.orden_peticion, fila.vacante))
    elif orden == "depurada":
        resultado.sort(
            key=lambda fila: (
                fila.vacante,
                depuracion.posicion[(fila.nio, fila.vacante)],
                fila.orden_parcial,
                fila.nio,
            )
        )
    elif orden == "nota":
        resultado.sort(
            key=lambda fila: (-fila.nota_final, fila.vacante, fila.orden_parcial, fila.nio)
        )
    else:
        resultado.sort(key=lambda fila: (fila.vacante, fila.orden_parcial, fila.nio))
    return resultado


def resumen_codigos(
    registros: Sequence[Registro], depuracion: Depuracion
) -> list[dict[str, object]]:
    por_codigo: dict[str, list[Registro]] = defaultdict(list)
    for registro in registros:
        por_codigo[registro.vacante].append(registro)

    resumen: list[dict[str, object]] = []
    for codigo in sorted(por_codigo):
        filas = sorted(por_codigo[codigo], key=lambda fila: fila.orden_parcial)
        plazas = filas[0].plazas
        resumen.append(
            {
                "vacante": codigo,
                "plazas": plazas,
                "aspirantes_incluidos": len(filas),
                "aspirantes_depurados": depuracion.aspirantes_por_codigo[codigo],
                "nota_maxima": filas[0].nota_final,
                "corte_depurado": depuracion.corte_por_codigo[codigo],
                "nota_minima": filas[-1].nota_final,
                "pagina_inicial": min(fila.pagina_pdf for fila in filas),
                "pagina_final": max(fila.pagina_pdf for fila in filas),
            }
        )
    return resumen


CSV_HEADERS = [
    "Vacante",
    "Plazas",
    "Nota final",
    "Orden petición",
    "Puntos concurso",
    "Fecha nacimiento",
    "Puesto bruto",
    "Puesto depurado",
    "En lista depurada",
    "Dentro plazas depuradas",
    "Margen depurado",
    "Estado depurado",
    "Código asignado",
    "Preferencia asignada",
    "Corte depurado",
    "Aspirantes depurados",
    "NIO",
    "Página PDF",
]


def fila_exportacion(
    registro: Registro, depuracion: Depuracion, para_excel: bool = False
) -> list[object]:
    puesto_depurado = depuracion.posicion[(registro.nio, registro.vacante)]
    asignada = depuracion.asignacion.get(registro.nio)
    corte = depuracion.corte_por_codigo[registro.vacante]
    if para_excel:
        nota: object = float(registro.nota_final)
        puntos: object = float(registro.puntos_concurso)
        fecha: object = registro.fecha_nacimiento
        corte_salida: object = float(corte) if corte is not None else None
    else:
        nota = texto_decimal_es(registro.nota_final)
        puntos = texto_decimal_es(registro.puntos_concurso)
        fecha = registro.fecha_nacimiento.strftime("%d-%m-%Y")
        corte_salida = texto_decimal_es(corte) if corte is not None else ""
    return [
        registro.vacante,
        registro.plazas,
        nota,
        registro.orden_peticion,
        puntos,
        fecha,
        registro.orden_parcial,
        puesto_depurado,
        "Sí" if (registro.nio, registro.vacante) in depuracion.en_lista else "No",
        "Sí" if puesto_depurado <= registro.plazas else "No",
        registro.plazas - puesto_depurado,
        estado_depurado(registro, depuracion),
        asignada[0] if asignada else "",
        asignada[1] if asignada else "",
        corte_salida,
        depuracion.aspirantes_por_codigo[registro.vacante],
        registro.nio,
        registro.pagina_pdf,
    ]


def exportar_csv(
    registros: Sequence[Registro], ruta: Path, depuracion: Depuracion
) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8-sig", newline="") as flujo:
        escritor = csv.writer(flujo, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        escritor.writerow(CSV_HEADERS)
        for registro in registros:
            escritor.writerow(fila_exportacion(registro, depuracion))


def validar_rutas_salida(ruta_pdf: Path, ruta_csv: Path | None, ruta_xlsx: Path | None) -> None:
    """Evita que una salida pueda sobrescribir el PDF u otra salida."""

    def normalizar(ruta: Path) -> str:
        return os.path.normcase(str(ruta.expanduser().resolve()))

    pdf = normalizar(ruta_pdf)
    csv = normalizar(ruta_csv) if ruta_csv else None
    xlsx = normalizar(ruta_xlsx) if ruta_xlsx else None
    if csv == pdf or xlsx == pdf:
        raise ValueError("La ruta de salida no puede ser la misma que la del PDF")
    if csv is not None and csv == xlsx:
        raise ValueError("--csv y --xlsx deben usar rutas diferentes")


def _estilo_hoja_datos(hoja, nombre_tabla: str) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.table import Table, TableStyleInfo

    hoja.sheet_view.showGridLines = False
    hoja.freeze_panes = "A2"
    hoja.auto_filter.ref = hoja.dimensions
    relleno = PatternFill("solid", fgColor="1F4E78")
    for celda in hoja[1]:
        celda.fill = relleno
        celda.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        celda.alignment = Alignment(horizontal="center", vertical="center")

    if hoja.max_row >= 2:
        tabla = Table(displayName=nombre_tabla, ref=hoja.dimensions)
        tabla.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        hoja.add_table(tabla)

    anchos = {
        "A": 11,
        "B": 9,
        "C": 12,
        "D": 15,
        "E": 17,
        "F": 18,
        "G": 14,
        "H": 17,
        "I": 18,
        "J": 24,
        "K": 16,
        "L": 36,
        "M": 18,
        "N": 21,
        "O": 17,
        "P": 21,
        "Q": 19,
        "R": 12,
    }
    for columna, ancho in anchos.items():
        hoja.column_dimensions[columna].width = ancho

    for fila in hoja.iter_rows(min_row=2):
        for celda in fila:
            celda.font = Font(name="Arial", size=10)
            celda.alignment = Alignment(horizontal="center", vertical="center")
        fila[0].number_format = "@"
        fila[2].number_format = "0.000"
        fila[4].number_format = "0.00"
        fila[5].number_format = "dd-mm-yyyy"
        fila[14].number_format = "0.000"
        fila[16].number_format = "@"
        estado = str(fila[11].value or "")
        if estado == "TITULAR":
            fila[11].fill = PatternFill("solid", fgColor="E2F0D9")
            fila[11].font = Font(name="Arial", size=10, bold=True, color="375623")
        elif estado == "TITULAR EN PREFERENCIA ANTERIOR":
            fila[11].fill = PatternFill("solid", fgColor="D9EAF7")
            fila[11].font = Font(name="Arial", size=10, bold=True, color="1F4E78")


def _crear_hoja_datos(
    libro,
    nombre: str,
    registros: Sequence[Registro],
    tabla: str,
    depuracion: Depuracion,
) -> None:
    hoja = libro.create_sheet(nombre)
    hoja.append(CSV_HEADERS)
    for registro in registros:
        hoja.append(fila_exportacion(registro, depuracion, para_excel=True))
    _estilo_hoja_datos(hoja, tabla)


def exportar_xlsx(
    registros: Sequence[Registro],
    ruta: Path,
    ruta_pdf: Path,
    diagnostico: Diagnostico,
    total_pdf: int,
    depuracion: Depuracion,
) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as exc:
        raise ExtractionError(
            "Falta openpyxl para crear XLSX. Instala las dependencias con: "
            "py -m pip install -r requirements.txt"
        ) from exc

    libro = Workbook()
    resumen = libro.active
    resumen.title = "Resumen"
    resumen.sheet_view.showGridLines = False
    nios = {fila.nio for fila in registros}
    es_personal = len(nios) == 1

    resumen.merge_cells("A2:H2")
    resumen.merge_cells("A3:H3")
    resumen["A2"] = (
        "Resultado depurado personal"
        if es_personal
        else "Listas depuradas de Tropa y Marinería"
    )
    resumen["A2"].font = Font(name="Arial", size=14, bold=True, color="1F1F1F")
    resumen["A3"] = "Adjudicación estable respetando el orden de preferencias"
    resumen["A3"].font = Font(name="Arial", size=10, italic=True, color="666666")
    resumen["A5"] = "Dato"
    resumen.merge_cells("A5:C5")
    resumen["D5"] = "Valor"
    resumen.merge_cells("D5:H5")

    metadatos = [
        ("PDF de origen", ruta_pdf.name),
        ("SHA-256", diagnostico.hash_sha256),
        ("Páginas del PDF", diagnostico.paginas_pdf),
        ("Registros del PDF completo", total_pdf),
        ("Registros incluidos", len(registros)),
        ("Códigos incluidos", len({fila.vacante for fila in registros})),
        ("NIO incluidos", len(nios)),
        ("Plazas adjudicadas en la simulación", len(depuracion.asignacion)),
    ]
    for numero_fila, (etiqueta, valor) in enumerate(metadatos, start=6):
        resumen.merge_cells(
            start_row=numero_fila, start_column=1, end_row=numero_fila, end_column=3
        )
        resumen.merge_cells(
            start_row=numero_fila, start_column=4, end_row=numero_fila, end_column=8
        )
        resumen.cell(numero_fila, 1, etiqueta)
        resumen.cell(numero_fila, 4, valor)

    aviso_fila = 16
    resumen.merge_cells(
        start_row=aviso_fila, start_column=1, end_row=aviso_fila + 1, end_column=3
    )
    resumen.merge_cells(
        start_row=aviso_fila, start_column=4, end_row=aviso_fila + 1, end_column=8
    )
    resumen.cell(aviso_fila, 1, "Interpretación")
    resumen.cell(
        aviso_fila,
        4,
        "El puesto depurado elimina a quienes obtienen una preferencia anterior. "
        "En opciones inferiores a la asignada muestra el puesto que se ocuparía si "
        "se perdiera la anterior. No incorpora aptitudes, renuncias ni reposiciones.",
    )
    resumen.cell(aviso_fila, 4).alignment = Alignment(wrap_text=True, vertical="center")

    for celda in resumen[5][:8]:
        celda.fill = PatternFill("solid", fgColor="1F4E78")
        celda.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        celda.alignment = Alignment(horizontal="center", vertical="center")
    anchos_resumen = {
        "A": 14,
        "B": 13,
        "C": 18,
        "D": 15,
        "E": 18,
        "F": 18,
        "G": 36,
        "H": 17,
    }
    for columna, ancho in anchos_resumen.items():
        resumen.column_dimensions[columna].width = ancho
    for numero_fila in range(6, 14):
        resumen.cell(numero_fila, 1).font = Font(name="Arial", size=10, bold=True)
        resumen.cell(numero_fila, 1).alignment = Alignment(vertical="center")
        resumen.cell(numero_fila, 4).font = Font(name="Arial", size=10)
        resumen.cell(numero_fila, 4).alignment = Alignment(vertical="center")
    resumen.cell(aviso_fila, 1).font = Font(name="Arial", size=10, bold=True)
    resumen.cell(aviso_fila, 1).alignment = Alignment(vertical="center")
    resumen.cell(aviso_fila, 4).font = Font(name="Arial", size=10)
    resumen.row_dimensions[aviso_fila].height = 25
    resumen.row_dimensions[aviso_fila + 1].height = 25

    fila_inicio = 19
    filas_resumen: list[list[object]] = []
    if es_personal:
        cabeceras_resumen = [
            "Preferencia",
            "Vacante",
            "Plazas",
            "Nota",
            "Puesto bruto",
            "Puesto depurado",
            "Estado",
            "Corte depurado",
        ]
        for registro in sorted(registros, key=lambda f: f.orden_peticion):
            corte = depuracion.corte_por_codigo[registro.vacante]
            filas_resumen.append(
                [
                    registro.orden_peticion,
                    registro.vacante,
                    registro.plazas,
                    float(registro.nota_final),
                    registro.orden_parcial,
                    depuracion.posicion[(registro.nio, registro.vacante)],
                    estado_depurado(registro, depuracion),
                    float(corte) if corte is not None else None,
                ]
            )
    else:
        cabeceras_resumen = [
            "Vacante",
            "Plazas",
            "Filas incluidas",
            "Aspirantes depurados",
            "Corte depurado",
            "Nota máxima incluida",
            "Nota mínima incluida",
            "Páginas",
        ]
        for item in resumen_codigos(registros, depuracion):
            corte = item["corte_depurado"]
            filas_resumen.append(
                [
                    item["vacante"],
                    item["plazas"],
                    item["aspirantes_incluidos"],
                    item["aspirantes_depurados"],
                    float(corte) if corte is not None else None,
                    float(item["nota_maxima"]),
                    float(item["nota_minima"]),
                    f"{item['pagina_inicial']}-{item['pagina_final']}",
                ]
            )

    for columna, cabecera in enumerate(cabeceras_resumen, start=1):
        resumen.cell(fila_inicio, columna, cabecera)
    for desplazamiento, valores in enumerate(filas_resumen, start=1):
        for columna, valor in enumerate(valores, start=1):
            resumen.cell(fila_inicio + desplazamiento, columna, valor)

    if filas_resumen:
        rango = f"A{fila_inicio}:H{fila_inicio + len(filas_resumen)}"
        tabla_resumen = Table(displayName="TablaResumenDepurado", ref=rango)
        tabla_resumen.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        resumen.add_table(tabla_resumen)
        resumen.row_dimensions[fila_inicio].height = 30
        for celda in resumen[fila_inicio]:
            celda.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
            celda.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
        for numero_fila in range(
            fila_inicio + 1, fila_inicio + len(filas_resumen) + 1
        ):
            for celda in resumen[numero_fila]:
                celda.font = Font(name="Arial", size=10)
                celda.alignment = Alignment(horizontal="center", vertical="center")
            if es_personal:
                resumen.cell(numero_fila, 2).number_format = "@"
                resumen.cell(numero_fila, 4).number_format = "0.000"
                resumen.cell(numero_fila, 8).number_format = "0.000"
                estado = str(resumen.cell(numero_fila, 7).value or "")
                if estado == "TITULAR":
                    resumen.cell(numero_fila, 7).fill = PatternFill("solid", fgColor="E2F0D9")
                    resumen.cell(numero_fila, 7).font = Font(
                        name="Arial", size=10, bold=True, color="375623"
                    )
                elif estado == "TITULAR EN PREFERENCIA ANTERIOR":
                    resumen.cell(numero_fila, 7).fill = PatternFill("solid", fgColor="D9EAF7")
                    resumen.cell(numero_fila, 7).font = Font(
                        name="Arial", size=10, bold=True, color="1F4E78"
                    )
            else:
                resumen.cell(numero_fila, 1).number_format = "@"
                for columna in (5, 6, 7):
                    resumen.cell(numero_fila, columna).number_format = "0.000"

    lista_depurada = sorted(
        (
            fila
            for fila in registros
            if (fila.nio, fila.vacante) in depuracion.en_lista
        ),
        key=lambda f: (
            f.vacante,
            depuracion.posicion[(f.nio, f.vacante)],
            f.orden_parcial,
            f.nio,
        ),
    )
    por_persona = sorted(registros, key=lambda f: (f.nio, f.orden_peticion, f.vacante))
    _crear_hoja_datos(
        libro,
        "Lista depurada",
        lista_depurada,
        "TablaListaDepurada",
        depuracion,
    )

    if len(registros) <= 50000:
        _crear_hoja_datos(
            libro, "Por persona", por_persona, "TablaPorPersona", depuracion
        )
    else:
        nota_fila = 18
        resumen.merge_cells(
            start_row=nota_fila, start_column=1, end_row=nota_fila, end_column=8
        )
        resumen.cell(
            nota_fila,
            1,
            "La vista Por persona no se duplicó porque la extracción supera 50.000 filas. "
            "Filtra por NIO o crea otra exportación con --persona.",
        )
        resumen.cell(nota_fila, 1).font = Font(name="Arial", size=9, italic=True)
        resumen.cell(nota_fila, 1).alignment = Alignment(wrap_text=True, vertical="center")
        resumen.row_dimensions[nota_fila].height = 30

    ruta.parent.mkdir(parents=True, exist_ok=True)
    libro.save(ruta)


def imprimir_resumen_global(
    registros: Sequence[Registro], diagnostico: Diagnostico, ruta_pdf: Path
) -> None:
    print(f"PDF: {ruta_pdf.name}")
    print(f"Paginas: {diagnostico.paginas_pdf:,}".replace(",", "."))
    print(f"Registros: {len(registros):,}".replace(",", "."))
    print(f"Codigos de vacante: {len({fila.vacante for fila in registros}):,}".replace(",", "."))
    print(f"Personas (NIO): {len({fila.nio for fila in registros}):,}".replace(",", "."))
    recuperadas = diagnostico.paginas_recuperadas_por_coordenadas
    if recuperadas:
        print("Paginas recuperadas por coordenadas: " + ", ".join(map(str, recuperadas)))


def imprimir_resumen_codigos(
    registros: Sequence[Registro], depuracion: Depuracion
) -> None:
    for item in resumen_codigos(registros, depuracion):
        nota_corte = item["corte_depurado"]
        corte = texto_decimal_es(nota_corte) if isinstance(nota_corte, Decimal) else "n.d."
        print(
            f"Codigo {item['vacante']}: {item['aspirantes_depurados']} aspirantes depurados, "
            f"{item['plazas']} plazas, corte depurado {corte}, "
            f"paginas {item['pagina_inicial']}-{item['pagina_final']}"
        )


def imprimir_tabla(
    registros: Sequence[Registro], limite: int, depuracion: Depuracion
) -> None:
    if limite < 0:
        raise ValueError("--limite no puede ser negativo")
    visibles = list(registros if limite == 0 else registros[:limite])
    if not visibles:
        return

    encabezado = (
        f"{'Codigo':<7} {'Plz':>3} {'Nota':>6} {'Pref':>4} "
        f"{'Bruto':>6} {'Dep.':>5} {'Dentro':>6} {'Estado':<14} {'NIO':<14} {'Pag':>4}"
    )
    print("\n" + encabezado)
    print("-" * len(encabezado))
    for fila in visibles:
        posicion = depuracion.posicion[(fila.nio, fila.vacante)]
        estado = estado_depurado(fila, depuracion)
        if estado == "TITULAR EN PREFERENCIA ANTERIOR":
            estado_corto = "PREF. ANTERIOR"
        elif estado == "ASIGNADO EN PREFERENCIA ANTERIOR":
            estado_corto = "PREF. ANTERIOR"
        else:
            estado_corto = estado
        print(
            f"{fila.vacante:<7} {fila.plazas:>3} {texto_decimal_es(fila.nota_final):>6} "
            f"{fila.orden_peticion:>4} {fila.orden_parcial:>6} {posicion:>5} "
            f"{('Si' if posicion <= fila.plazas else 'No'):>6} "
            f"{estado_corto:<14} "
            f"{fila.nio:<14} {fila.pagina_pdf:>4}"
        )
    if len(registros) > len(visibles):
        print(
            f"\nMostradas {len(visibles)} de {len(registros)} filas. "
            "La exportacion contiene todas; usa --limite 0 para mostrarlas."
        )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tropa_pdf.py",
        description=(
            "Lee el PDF oficial de seleccion previa de Tropa y Marineria, "
            "busca por NIO o codigo de vacante y exporta CSV/XLSX."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  py tropa_pdf.py lista.pdf --codigo 60000 60001\n"
            "  py tropa_pdf.py lista.pdf --persona 20261-00-00000\n"
            "  py tropa_pdf.py lista.pdf --codigo 60000 --csv resultados.csv --xlsx resultados.xlsx"
        ),
    )
    parser.add_argument("pdf", type=Path, help="Ruta al PDF oficial")
    parser.add_argument(
        "--codigo",
        action="append",
        nargs="+",
        metavar="CODIGO",
        help="Uno o varios codigos de vacante; se puede repetir",
    )
    parser.add_argument(
        "--persona",
        metavar="NIO",
        help="NIO completo o fragmento. El PDF no contiene nombres.",
    )
    parser.add_argument("--preferencia", type=int, choices=range(1, 11))
    parser.add_argument(
        "--fecha-nacimiento",
        type=fecha_es,
        metavar="DD-MM-AAAA",
        help="Filtro adicional por fecha de nacimiento",
    )
    parser.add_argument(
        "--nota",
        type=decimal_es,
        metavar="NOTA",
        help="Filtro adicional por nota, por ejemplo 5,500",
    )
    parser.add_argument(
        "--solo-cupo-bruto",
        action="store_true",
        help="Conserva solo Ord.Parc. <= Plazas (no equivale a asignacion final)",
    )
    parser.add_argument(
        "--solo-cupo-depurado",
        action="store_true",
        help="Conserva solo las filas cuyo puesto depurado queda dentro de plazas",
    )
    parser.add_argument(
        "--incluir-excluidos",
        action="store_true",
        help=(
            "Con --codigo, incluye tambien a quienes obtienen una preferencia anterior. "
            "Por defecto se muestra la lista depurada real."
        ),
    )
    parser.add_argument(
        "--orden",
        choices=("automatico", "depurada", "vacante", "persona", "nota"),
        default="automatico",
        help=(
            "Orden en pantalla y CSV; automatico usa persona si hay --persona. "
            "El Excel incluye ambas vistas hasta 50.000 filas; en exportaciones "
            "mayores conserva Por vacante."
        ),
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=40,
        help="Filas que se muestran en pantalla (0 = todas). No limita exportaciones.",
    )
    parser.add_argument("--csv", type=Path, help="Ruta del CSV de salida")
    parser.add_argument("--xlsx", type=Path, help="Ruta del Excel de salida")
    parser.add_argument(
        "--diagnostico",
        action="store_true",
        help="Muestra SHA-256 y datos tecnicos de integridad",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    try:
        if args.limite < 0:
            raise ValueError("--limite no puede ser negativo")
        if args.nota is not None and not Decimal("0") <= args.nota <= Decimal("10"):
            raise ValueError("--nota debe estar entre 0 y 10")
        validar_rutas_salida(args.pdf, args.csv, args.xlsx)
        print("Leyendo y validando el PDF. Puede tardar unos segundos...")
        registros, diagnostico = extraer_registros(args.pdf)
        imprimir_resumen_global(registros, diagnostico, args.pdf)
        print("Calculando la adjudicacion depurada por preferencias...")
        depuracion = calcular_depuracion(registros)
        print(
            f"Plazas adjudicadas en la simulacion: "
            f"{len(depuracion.asignacion):,}".replace(",", ".")
        )

        filtrados = filtrar_registros(registros, args, depuracion)
        hay_filtros = any(
            (
                args.codigo,
                args.persona,
                args.preferencia is not None,
                args.fecha_nacimiento is not None,
                args.nota is not None,
                args.solo_cupo_bruto,
                args.solo_cupo_depurado,
            )
        )
        if not filtrados:
            print("\nNo hay resultados para los filtros indicados.", file=sys.stderr)
            return 2

        if hay_filtros:
            print(f"\nResultados filtrados: {len(filtrados)}")
            if not args.persona:
                imprimir_resumen_codigos(filtrados, depuracion)
            imprimir_tabla(filtrados, args.limite, depuracion)
        else:
            print(
                "\nNo se aplicaron filtros. Usa --codigo o --persona para ver una tabla; "
                "si exportas ahora, se incluiran todos los registros."
            )

        if args.csv:
            exportar_csv(filtrados, args.csv, depuracion)
            print(f"\nCSV guardado: {args.csv.resolve()}")
        if args.xlsx:
            exportar_xlsx(
                filtrados,
                args.xlsx,
                args.pdf,
                diagnostico,
                len(registros),
                depuracion,
            )
            print(f"Excel guardado: {args.xlsx.resolve()}")

        print(
            "\nEl puesto depurado elimina en cascada a quienes obtienen una "
            "preferencia anterior. No incorpora aptitudes, renuncias ni "
            "reposiciones posteriores."
        )
        if args.diagnostico:
            print(f"SHA-256: {diagnostico.hash_sha256}")
        return 0
    except (ExtractionError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

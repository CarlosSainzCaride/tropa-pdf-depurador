# Lector del PDF de Tropa y Marinería

Este programa lee el PDF oficial de selección previa para la Fase 2 y permite:

- buscar una persona por su NIO y ordenar sus códigos por preferencia;
- calcular la adjudicación depurada respetando las preferencias de todos los aspirantes;
- filtrar códigos y devolver por defecto la lista depurada, no el ranking bruto;
- filtrar por nota, fecha de nacimiento u orden de petición;
- exportar el resultado completo a CSV y Excel;
- detectar filas mal extraídas y recuperar automáticamente las páginas ambiguas por coordenadas.

El PDF no contiene nombres. La búsqueda por persona se hace mediante el NIO.

La depuración se resuelve de forma global: cada aspirante solicita sus vacantes por orden de preferencia, cada código conserva a quienes tienen mejor orden oficial hasta cubrir sus plazas y los rechazados pasan a la opción siguiente. El proceso continúa hasta que no quedan cambios. Después se eliminan de cada código las personas que obtienen una preferencia anterior.

## Instalación en Windows

Necesitas Python 3.10 o posterior. Durante la instalación de Python, activa la opción **Add Python to PATH**.

Abre PowerShell en la carpeta que contiene estos archivos y ejecuta:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No hace falta activar el entorno virtual.

## Ejemplos de uso

Buscar dos códigos de ejemplo y crear CSV y Excel:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --codigo 60000 60001 --csv .\resultados.csv --xlsx .\resultados.xlsx
```

Buscar todas las preferencias de una persona:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --persona 20261-00-00000
```

Exportar las preferencias de esa persona:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --persona 20261-00-00000 --csv .\preferencias.csv --xlsx .\preferencias.xlsx
```

Mostrar solo quienes pusieron un código como primera preferencia:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --codigo 60000 --preferencia 1
```

Incluir también, para auditoría, las filas retiradas porque la persona obtiene una preferencia anterior:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --codigo 60000 --incluir-excluidos
```

Mostrar únicamente posiciones que quedan dentro del número de plazas tras depurar:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --codigo 60000 --solo-cupo-depurado
```

Buscar por fecha y nota cuando no se conoce el NIO:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --fecha-nacimiento 01-01-2000 --nota 5,500
```

Mostrar todas las filas en pantalla en vez de las primeras 40:

```powershell
.\.venv\Scripts\python.exe .\tropa_pdf.py "C:\ruta\lista.pdf" --codigo 60001 --limite 0
```

La opción `--limite` solo afecta a la pantalla. El CSV y el Excel siempre incluyen todas las filas filtradas.

## Cómo interpreta los datos

- **Vacante**: código de la especialidad o destino.
- **Plazas**: plazas publicadas para el código.
- **Nota final**: nota que figura para esa persona y ese código. Puede variar entre códigos por los puntos de concurso.
- **Orden petición**: prioridad que dio la persona a ese código.
- **Puesto bruto**: posición oficial `Ord.Parc.` dentro de ese código en el PDF.
- **Puesto depurado**: posición después de retirar en cascada a quienes obtienen una preferencia anterior.
- **En lista depurada**: indica si la fila permanece realmente en ese código.
- **Dentro plazas depuradas**: indica si el puesto depurado queda dentro del número de plazas.
- **Estado depurado**: distingue `TITULAR`, `RESERVA` y los casos que ya tienen una preferencia anterior.
- **Corte depurado**: nota de la persona que ocupa el último puesto disponible tras la depuración.
- **Página PDF**: página donde aparece la fila, para poder comprobarla visualmente.

El Excel contiene una hoja de resumen, la **Lista depurada** y, para extracciones de hasta 50.000 filas, una vista **Por persona**. En una búsqueda por NIO, el resumen muestra todas las preferencias en su orden correcto, con puesto bruto, puesto depurado, estado y corte. El CSV usa UTF-8 con BOM y separador `;`, por lo que se abre correctamente en Excel en configuraciones españolas.

La opción `--orden` controla la tabla de pantalla y el CSV. Una consulta por persona se ordena por preferencia; una consulta por código se ordena por puesto depurado. El programa también impide usar como salida la propia ruta del PDF o la misma ruta para CSV y XLSX.

## Limitación importante

Este PDF es la **selección previa para pasar a la Fase 2**. La posición depurada calculada supone que todas las personas del PDF siguen aptas y disponibles. No puede incorporar resultados médicos o físicos, renuncias, incidencias ni reposiciones posteriores que no estén incluidas en el documento.

Para respetar la privacidad de los aspirantes, todo el proceso se realiza localmente y no envía el PDF ni los resultados a Internet.

# Monitoreo de pasajes · Concepción → Portugal, julio 2027

Registro diario de precios de Google Flights (vía [SerpApi](https://serpapi.com/google-flights-api)) para
decidir cuándo comprar. El dashboard está en `index.html` y se publica en GitHub Pages.

## Qué se consulta

- 2 adultos + 1 niño, desde CCP, ida 12 jul y vuelta 31 jul 2027, con ±2 días en cada extremo.
- Destinos: Oporto, Lisboa y los dos itinerarios que llegan a una ciudad y vuelven desde la otra.
- **Todos los días** (2 créditos): fechas base a OPO y a LIS.
- **Por turnos** (4 créditos diarios): las 48 combinaciones restantes de fechas y los 2 itinerarios combinados.
  La vuelta completa toma 12,5 días.
- **Domingos** (6 créditos): tarifa y equipaje de la opción más barata de las 3 rutas más económicas.
- Ruta = primera escala fuera de Chile: Madrid, Brasil, Lima, París u otra.

Presupuesto: unos 212 créditos al mes, dentro de los 250 del plan gratuito. Si quedan menos de 12 créditos,
solo se consultan las fechas base.

## Archivos

| Archivo | Contenido |
|---|---|
| `config.json` | pasajeros, fechas, holgura, maletas, corredores, presupuesto |
| `monitor.py` | consulta, destila y regenera el dashboard (solo biblioteca estándar) |
| `plantilla.html` | dashboard; `index.html` es la plantilla con los datos incrustados |
| `data/consultas.csv` | una fila por llamada: estado, mínimo y créditos restantes |
| `data/opciones.csv` | una fila por alternativa de vuelo que devolvió Google |
| `data/insights.csv` | nivel y rango típico que informa Google |
| `data/historial_google.csv` | serie de precios que Google guarda por búsqueda |
| `data/maletas.csv` | tarifas y equipaje (lectura de los domingos) |
| `data/raw/AAAA-MM-DD/*.json.gz` | respuestas originales, sin la clave |

## Uso local

```bash
python -X utf8 monitor.py plan --dias 5
python -X utf8 monitor.py factibilidad
python -X utf8 monitor.py dashboard
```

La clave se lee de `SERPAPI_KEY` o de `~/.secrets/serpapi.txt`, que queda **fuera** del repo. En GitHub va
como secreto del repositorio con el nombre `SERPAPI_KEY`.

El registro lo escribe GitHub Actions: antes de correr algo en local que modifique `data/`, hacer `git pull`.

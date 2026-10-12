# jira-consult

CLI en Python para consultar, actualizar y comentar tickets de Jira desde la terminal, con utilitarios para generar contenido con el formato nativo de Jira (código, tablas, paneles, colapsables, colores, etc.).

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuración

Copia `.env.example` a `.env` y completa tus credenciales:

```bash
cp .env.example .env
```

```
JIRA_BASE_URL=https://tuempresa.atlassian.net
JIRA_TENANT_SESSION_TOKEN=valor_de_la_cookie_tenant.session.token
```

El valor de `JIRA_TENANT_SESSION_TOKEN` es la cookie `tenant.session.token` de tu sesión en Jira:

1. Abre tu instancia de Jira en Chrome e inicia sesión.
2. Abre DevTools (`Cmd+Option+I`) → pestaña **Almacenamiento** → **Cookies** → `https://tuempresa.atlassian.net`.
3. Copia el **valor** de la cookie `tenant.session.token`.

> Esta cookie es una sesión activa de tu navegador: trátala como una contraseña, no la subas al repositorio y no la compartas. Caduca cuando se cierra la sesión en Jira; si el CLI empieza a responder `401`, copia una nueva.

> Este CLI está pensado para **Jira Cloud**.

Verifica que las credenciales funcionan sin tocar ningún ticket:

```bash
python main.py whoami
```

---

## Consultar un ticket (`get`)

```bash
python main.py get KAN-1
```

Muestra: clave, resumen, tipo, estado, prioridad, asignado, informador, fechas, etiquetas, componentes, versiones, **descripción** (renderizada desde el wiki-markup de Jira a texto legible en terminal), **subtareas**, **tickets enlazados**, **adjuntos**, **comentarios** (también renderizados) y **campos personalizados** (con su nombre resuelto, no solo el id `customfield_XXXXX`).

Opciones:

| Flag | Descripción |
|---|---|
| `--raw` | Imprime el JSON completo que devuelve Jira, sin procesar |

---

## Actualizar un ticket (`update`)

```bash
python main.py update KAN-1 --set summary="Nuevo título"
```

Puedes combinar varias formas de indicar los campos a cambiar:

| Flag | Uso |
|---|---|
| `--set campo=valor` | Repetible. Ej: `--set summary="Nuevo título" --set labels=urgente` |
| `--json '{"campo": "valor"}'` | Para payloads más complejos (listas, objetos) |
| `--field NOMBRE --file ruta.txt` | Para contenido largo/multilínea (ideal para `description`) |

**El campo se puede indicar por su nombre visible, no solo por su id.** Por ejemplo, si tu instancia tiene un campo custom llamado "Story Points" con id `customfield_10050`, puedes hacer directamente:

```bash
python main.py update KAN-1 --set "Story Points=5"
```

Internamente el CLI consulta `/rest/api/2/field` y resuelve el nombre al id correcto — no necesitas mantener esa relación a mano. Usa el comando `fields` (ver abajo) para saber qué nombres existen.

Antes de escribir de verdad, puedes revisar a qué campos se resolvió todo con `--dry-run` (no llama a Jira, solo imprime el resultado):

```bash
python main.py update KAN-1 --set "Story Points=5" --dry-run
# -> {"customfield_10050": "5"}
```

### Actualizar la descripción con contenido formateado

```bash
python main.py format panel --title Info --bg-color "#deebff" --text "Contenido" > /tmp/desc.txt
python main.py update KAN-1 --field description --file /tmp/desc.txt
```

O en una sola línea con sustitución de comandos:

```bash
python main.py update KAN-1 --set description="$(python main.py format bold --text 'Todo OK')"
```

---

## Listar campos de Jira (`fields`)

Útil para encontrar el nombre o id de un campo custom/reservado (sobre todo al migrar de una instancia piloto a la real de la empresa, donde los ids cambian).

```bash
python main.py fields                 # todos los campos
python main.py fields --search story   # filtra por nombre o id
```

---

## Agregar un comentario (`comment`)

```bash
python main.py comment KAN-1 --text "Listo, ya quedó desplegado."
```

```bash
python main.py comment KAN-1 --file /tmp/comentario.txt
```

Si no pasas `--text` ni `--file`, lee el contenido desde stdin:

```bash
python main.py format quote --text "Cita de ejemplo" | python main.py comment KAN-1
```

---

## Generar contenido con formato de Jira (`format`)

Cada subcomando imprime el marcado (wiki-markup) listo para usar en `update --set/--field` o en `comment`. El texto se da con `--text`, `--file`, o por stdin.

| Subcomando | Ejemplo |
|---|---|
| `code` | `format code --lang python --text "print(1)"` |
| `panel` | `format panel --title Info --bg-color "#deebff" --text "..."` |
| `expand` | `format expand --title "Ver más" --text "..."` |
| `quote` | `format quote --text "..."` |
| `list` | `format list --item "uno" --item "dos"` |
| `numlist` | `format numlist --item "paso 1" --item "paso 2"` |
| `table` | `format table --headers "Nombre,Edad" --row "Ana,30" --row "Luis,25"` |
| `color` | `format color --color "#FF0000" --text "..."` |
| `heading` | `format heading --level 2 --text "..."` |
| `divider` | `format divider` |
| `link` | `format link --text "Documentación" --url "https://..."` |
| `bold` / `italic` / `underline` / `strike` / `mono` | `format bold --text "..."` |

Puedes combinarlos concatenando salidas (todo es texto plano):

```bash
PANEL=$(python main.py format panel --title Resultado --text "$(python main.py format bold --text 'Todo OK')")
python main.py update KAN-1 --set description="$PANEL"
```

### El colapsable (`expand`) es especial

El wiki-markup clásico de Jira **no tiene** un macro para bloques colapsables (eso es un macro de Confluence). Para que `{expand}` funcione como un bloque colapsable real en el editor nuevo de Jira, el CLI detecta automáticamente cuando el contenido de `update`/`comment` incluye un bloque `format expand` y, por detrás, hace una conversión en dos fases a ADF (el formato nativo de Jira) para crear un nodo `expand` de verdad. No requiere nada especial de tu parte, solo usar `format expand` normalmente.

---

## Limitaciones conocidas

- **Colores de panel**: Jira Cloud solo soporta 5 tipos semánticos de panel (`info`, `note`, `success`, `warning`, `error`). El `--bg-color` que pasas se mapea al tipo más parecido; no se preserva el hex exacto.
- El renderizado en terminal (`get`) es una aproximación para lectura rápida, no un reflejo pixel-perfect de cómo se ve en la interfaz web de Jira. Para verificar el resultado final, revisa el ticket en el navegador.

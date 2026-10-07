import argparse
import json
import sys

import requests

import jira_markup
from jira_client import JiraClient, JiraConfigError


# ---------------------------------------------------------------------------
# whoami
# ---------------------------------------------------------------------------

def cmd_whoami(args):
    client = JiraClient()
    me = client.whoami()
    print(f"Conectado a: {client.base_url}")
    print(f"Usuario:     {me.get('displayName')}")
    print(f"Email:       {me.get('emailAddress')}")
    print(f"Account ID:  {me.get('accountId')}")


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

def cmd_get(args):
    client = JiraClient()
    issue = client.get_issue(args.issue_key)

    if args.raw:
        print(json.dumps(issue, indent=2, ensure_ascii=False))
        return

    fields = issue.get("fields", {})
    status = (fields.get("status") or {}).get("name")
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    issuetype = (fields.get("issuetype") or {}).get("name")
    priority = (fields.get("priority") or {}).get("name")

    rows = [
        ("Clave", "key", issue.get("key")),
        ("Resumen", "summary", fields.get("summary")),
        ("Tipo", "issuetype", issuetype),
        ("Estado", "status", status),
        ("Prioridad", "priority", priority),
        ("Asignado a", "assignee", assignee.get("displayName", "-")),
        ("Reportado por", "reporter", reporter.get("displayName", "-")),
        ("Creado", "created", fields.get("created")),
        ("Actualizado", "updated", fields.get("updated")),
    ]

    labels = fields.get("labels") or []
    if labels:
        rows.append(("Etiquetas", "labels", ", ".join(labels)))

    components = [c.get("name") for c in (fields.get("components") or [])]
    if components:
        rows.append(("Componentes", "components", ", ".join(components)))

    fix_versions = [v.get("name") for v in (fields.get("fixVersions") or [])]
    if fix_versions:
        rows.append(("Versiones fix", "fixVersions", ", ".join(fix_versions)))

    label_width = max(len(f"{label} ({key}):") for label, key, _ in rows) + 1
    for label, key, value in rows:
        print(f"{f'{label} ({key}):':<{label_width}} {value}")

    print("\nDescripción:")
    print(jira_markup.render_for_terminal(fields.get("description")))

    _print_subtasks(fields.get("subtasks"))
    _print_issuelinks(fields.get("issuelinks"))
    _print_attachments(fields.get("attachment"))
    _print_comments(fields.get("comment"))
    _print_custom_fields(client, fields)


def _print_subtasks(subtasks):
    if not subtasks:
        return
    print("\nSubtareas:")
    for sub in subtasks:
        sub_fields = sub.get("fields", {})
        sub_status = (sub_fields.get("status") or {}).get("name")
        print(f"  - {sub.get('key')} [{sub_status}] {sub_fields.get('summary')}")


def _print_issuelinks(issuelinks):
    if not issuelinks:
        return
    print("\nTickets enlazados:")
    for link in issuelinks:
        link_type = link.get("type", {})
        if "outwardIssue" in link:
            relation = link_type.get("outward", "relacionado con")
            linked = link["outwardIssue"]
        elif "inwardIssue" in link:
            relation = link_type.get("inward", "relacionado con")
            linked = link["inwardIssue"]
        else:
            continue
        linked_fields = linked.get("fields", {})
        linked_status = (linked_fields.get("status") or {}).get("name")
        print(f"  - {relation}: {linked.get('key')} [{linked_status}] {linked_fields.get('summary')}")


def _print_attachments(attachments):
    if not attachments:
        return
    print("\nAdjuntos:")
    for att in attachments:
        author = (att.get("author") or {}).get("displayName", "-")
        print(f"  - {att.get('filename')} ({att.get('size')} bytes, subido por {author})")


def _print_comments(comment_field):
    comments = (comment_field or {}).get("comments") or []
    if not comments:
        return
    print(f"\nComentarios ({len(comments)}):")
    for c in comments:
        author = (c.get("author") or {}).get("displayName", "-")
        print(f"\n--- {author} | {c.get('created')} ---")
        print(jira_markup.render_for_terminal(c.get("body")))


def _format_custom_value(value):
    if isinstance(value, dict):
        return value.get("value") or value.get("name") or json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return ", ".join(_format_custom_value(v) for v in value) if value else "-"
    return str(value)


def _print_custom_fields(client, fields):
    custom_items = {k: v for k, v in fields.items() if k.startswith("customfield_") and v is not None}
    if not custom_items:
        return

    try:
        id_to_name = {f["id"]: f.get("name", f["id"]) for f in client.get_fields()}
    except requests.HTTPError:
        id_to_name = {}

    print("\nCampos personalizados:")
    for field_id, value in custom_items.items():
        name = id_to_name.get(field_id, field_id)
        print(f"  - {name} ({field_id}): {_format_custom_value(value)}")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def cmd_update(args):
    fields = {}

    if args.json:
        fields.update(json.loads(args.json))

    if args.field and args.file:
        with open(args.file, encoding="utf-8") as f:
            fields[args.field] = f.read()

    for item in args.set or []:
        if "=" not in item:
            raise ValueError(f"Formato inválido en --set '{item}', usa clave=valor")
        key, value = item.split("=", 1)
        fields[key] = value

    if not fields:
        raise ValueError("No se especificó ningún campo a actualizar (usa --set, --json o --field/--file)")

    client = JiraClient()
    fields = _resolve_field_keys(client, fields)

    if args.dry_run:
        print(json.dumps(fields, indent=2, ensure_ascii=False))
        return

    client.update_issue_with_markup(args.issue_key, fields)
    print(f"Ticket {args.issue_key} actualizado correctamente.")


def _resolve_field_keys(client, fields):
    """Permite usar el nombre visible del campo (ej. 'Story Points') en vez
    del id interno (ej. customfield_10050), para no depender de esa relación
    manual al trabajar con campos custom/reservados de cada instancia de Jira."""
    by_id, by_name = {}, {}
    for f in client.get_fields():
        by_id[f["id"].lower()] = f["id"]
        if f.get("name"):
            by_name[f["name"].lower()] = f["id"]

    resolved = {}
    for key, value in fields.items():
        lk = key.lower()
        resolved[by_id.get(lk) or by_name.get(lk) or key] = value
    return resolved


# ---------------------------------------------------------------------------
# fields
# ---------------------------------------------------------------------------

def cmd_fields(args):
    client = JiraClient()
    all_fields = client.get_fields()

    if args.search:
        term = args.search.lower()
        all_fields = [
            f for f in all_fields
            if term in (f.get("name") or "").lower() or term in f["id"].lower()
        ]

    all_fields.sort(key=lambda f: (f.get("custom", False), (f.get("name") or "").lower()))

    print(f"{'ID':<25} {'TIPO':<10} {'ESQUEMA':<14} NOMBRE")
    for f in all_fields:
        kind = "custom" if f.get("custom") else "sistema"
        schema_type = (f.get("schema") or {}).get("type", "-")
        print(f"{f['id']:<25} {kind:<10} {schema_type:<14} {f.get('name')}")


# ---------------------------------------------------------------------------
# comment
# ---------------------------------------------------------------------------

def cmd_comment(args):
    body = _read_text(args)
    if not body:
        raise ValueError("El comentario no puede estar vacío")

    client = JiraClient()
    client.add_comment_with_markup(args.issue_key, body)
    print(f"Comentario agregado al ticket {args.issue_key}.")


# ---------------------------------------------------------------------------
# format (utilitarios de marcado de Jira)
# ---------------------------------------------------------------------------

def _read_text(args):
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as f:
            return f.read().rstrip("\n")
    if getattr(args, "text", None) is not None:
        return args.text
    return sys.stdin.read().rstrip("\n")


def cmd_format_code(args):
    print(jira_markup.code_block(_read_text(args), args.lang))


def cmd_format_panel(args):
    print(jira_markup.panel(
        _read_text(args),
        title=args.title,
        bg_color=args.bg_color,
        border_color=args.border_color,
        title_bg_color=args.title_bg_color,
    ))


def cmd_format_expand(args):
    print(jira_markup.expand(_read_text(args), title=args.title))


def cmd_format_quote(args):
    print(jira_markup.quote_block(_read_text(args)))


def cmd_format_list(args):
    if not args.item:
        raise ValueError("Especifica al menos un --item")
    print(jira_markup.bullet_list(args.item))


def cmd_format_numlist(args):
    if not args.item:
        raise ValueError("Especifica al menos un --item")
    print(jira_markup.numbered_list(args.item))


def cmd_format_table(args):
    headers = [h.strip() for h in args.headers.split(",")]
    rows = [[c.strip() for c in row.split(",")] for row in (args.row or [])]
    print(jira_markup.table(headers, rows))


def cmd_format_color(args):
    print(jira_markup.color(_read_text(args), args.color))


def cmd_format_heading(args):
    print(jira_markup.heading(_read_text(args), args.level))


def cmd_format_divider(args):
    print(jira_markup.divider())


def cmd_format_link(args):
    print(jira_markup.link(args.text, args.url))


def cmd_format_bold(args):
    print(jira_markup.bold(_read_text(args)))


def cmd_format_italic(args):
    print(jira_markup.italic(_read_text(args)))


def cmd_format_underline(args):
    print(jira_markup.underline(_read_text(args)))


def cmd_format_strike(args):
    print(jira_markup.strikethrough(_read_text(args)))


def cmd_format_mono(args):
    print(jira_markup.monospace(_read_text(args)))


def _add_text_or_file(parser, required=True):
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--text", help="Contenido directo")
    group.add_argument("--file", help="Ruta a un archivo con el contenido")


def build_parser():
    parser = argparse.ArgumentParser(
        description="CLI para consultar, actualizar y comentar tickets de Jira."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    whoami_parser = subparsers.add_parser(
        "whoami", help="Verifica la conexión y credenciales con Jira (solo lectura, no modifica nada)"
    )
    whoami_parser.set_defaults(func=cmd_whoami)

    get_parser = subparsers.add_parser("get", help="Consulta el contenido de un ticket de Jira")
    get_parser.add_argument("issue_key", help="Clave del ticket, ej. PROJ-123")
    get_parser.add_argument("--raw", action="store_true", help="Muestra la respuesta completa en JSON")
    get_parser.set_defaults(func=cmd_get)

    update_parser = subparsers.add_parser("update", help="Actualiza campos de un ticket de Jira")
    update_parser.add_argument("issue_key", help="Clave del ticket, ej. PROJ-123")
    update_parser.add_argument(
        "--set", action="append", metavar="campo=valor",
        help="Campo a actualizar, ej. --set summary='Nuevo título' (repetible)",
    )
    update_parser.add_argument("--json", help='JSON con los campos a actualizar')
    update_parser.add_argument("--field", help="Nombre del campo a actualizar (usar junto con --file)")
    update_parser.add_argument("--file", help="Archivo con el contenido para --field")
    update_parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra los campos ya resueltos (nombre -> id) sin modificar Jira",
    )
    update_parser.set_defaults(func=cmd_update)

    fields_parser = subparsers.add_parser(
        "fields", help="Lista los campos de Jira (id, nombre, tipo) para ubicar campos custom/reservados"
    )
    fields_parser.add_argument("--search", help="Filtra por nombre o id, ej. --search 'story'")
    fields_parser.set_defaults(func=cmd_fields)

    comment_parser = subparsers.add_parser("comment", help="Agrega un comentario a un ticket de Jira")
    comment_parser.add_argument("issue_key", help="Clave del ticket, ej. PROJ-123")
    _add_text_or_file(comment_parser, required=False)
    comment_parser.set_defaults(func=cmd_comment)

    format_parser = subparsers.add_parser(
        "format", help="Genera fragmentos de marcado de Jira (código, tabla, panel, etc.)"
    )
    format_sub = format_parser.add_subparsers(dest="format_type", required=True)

    p = format_sub.add_parser("code", help="Bloque de código")
    p.add_argument("--lang", help="Lenguaje, ej. python, java, json")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_code)

    p = format_sub.add_parser("panel", help="Panel informativo")
    p.add_argument("--title")
    p.add_argument("--bg-color", dest="bg_color", help="Color de fondo, ej. #deebff")
    p.add_argument("--border-color", dest="border_color")
    p.add_argument("--title-bg-color", dest="title_bg_color")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_panel)

    p = format_sub.add_parser("expand", help="Bloque colapsable (ampliar)")
    p.add_argument("--title")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_expand)

    p = format_sub.add_parser("quote", help="Cita")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_quote)

    p = format_sub.add_parser("list", help="Lista con viñetas")
    p.add_argument("--item", action="append", help="Elemento de la lista (repetible)")
    p.set_defaults(func=cmd_format_list)

    p = format_sub.add_parser("numlist", help="Lista numerada")
    p.add_argument("--item", action="append", help="Elemento de la lista (repetible)")
    p.set_defaults(func=cmd_format_numlist)

    p = format_sub.add_parser("table", help="Tabla")
    p.add_argument("--headers", required=True, help="Encabezados separados por coma, ej. Nombre,Edad")
    p.add_argument("--row", action="append", help="Fila separada por coma (repetible)")
    p.set_defaults(func=cmd_format_table)

    p = format_sub.add_parser("color", help="Texto con color")
    p.add_argument("--color", required=True, help="ej. #FF0000, red, blue")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_color)

    p = format_sub.add_parser("heading", help="Encabezado (h1-h6)")
    p.add_argument("--level", type=int, default=1)
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_heading)

    p = format_sub.add_parser("divider", help="Línea divisora")
    p.set_defaults(func=cmd_format_divider)

    p = format_sub.add_parser("link", help="Enlace")
    p.add_argument("--text", required=True)
    p.add_argument("--url", required=True)
    p.set_defaults(func=cmd_format_link)

    p = format_sub.add_parser("bold", help="Texto en negrita")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_bold)

    p = format_sub.add_parser("italic", help="Texto en cursiva")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_italic)

    p = format_sub.add_parser("underline", help="Texto subrayado")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_underline)

    p = format_sub.add_parser("strike", help="Texto tachado")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_strike)

    p = format_sub.add_parser("mono", help="Texto monoespaciado")
    _add_text_or_file(p)
    p.set_defaults(func=cmd_format_mono)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except JiraConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        sys.exit(2)
    except requests.HTTPError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()

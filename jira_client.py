import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

# El wiki-markup clásico de Jira no tiene un macro para bloques colapsables
# (ese es un macro de Confluence); {expand} solo existe como nodo nativo de
# ADF en el editor nuevo. Para lograrlo, se sustituye cada bloque {expand} por
# marcadores de texto, se deja que Jira convierta el resto del wiki-markup a
# ADF normalmente (v2), y luego se reemplazan esos marcadores por un nodo
# "expand" real editando el documento ADF (v3).
_EXPAND_BLOCK_RE = re.compile(
    r"\{expand(?::title=(?P<title>[^}]*))?\}\s*\n?(?P<body>.*?)\n?\{expand\}",
    re.DOTALL,
)


class JiraConfigError(Exception):
    pass


class JiraClient:
    def __init__(self, base_url=None, session_token=None):
        self.base_url = (base_url or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        self.session_token = session_token or os.getenv("JIRA_TENANT_SESSION_TOKEN")

        if not all([self.base_url, self.session_token]):
            raise JiraConfigError(
                "Faltan credenciales. Define JIRA_BASE_URL y JIRA_TENANT_SESSION_TOKEN "
                "(en variables de entorno o en un archivo .env)."
            )

        # Autenticación con la cookie de sesión del navegador (tenant.session.token),
        # en lugar de Basic Auth con email + API token.
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Cookie": f"tenant.session.token={self.session_token}",
        })
        self._fields_cache = None

    def get_issue(self, issue_key, fields=None):
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}"
        params = {}
        if fields:
            params["fields"] = ",".join(fields)

        response = self.session.get(url, params=params, timeout=30)
        self._raise_for_status(response)
        return response.json()

    def update_issue(self, issue_key, fields: dict):
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}"
        payload = {"fields": fields}

        response = self.session.put(url, json=payload, timeout=30)
        self._raise_for_status(response)
        return True

    def whoami(self):
        url = f"{self.base_url}/rest/api/2/myself"
        response = self.session.get(url, timeout=30)
        self._raise_for_status(response)
        return response.json()

    def get_fields(self):
        if self._fields_cache is None:
            url = f"{self.base_url}/rest/api/2/field"
            response = self.session.get(url, timeout=30)
            self._raise_for_status(response)
            self._fields_cache = response.json()
        return self._fields_cache

    def add_comment(self, issue_key, body: str):
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}/comment"
        response = self.session.post(url, json={"body": body}, timeout=30)
        self._raise_for_status(response)
        return response.json()

    def update_issue_with_markup(self, issue_key, fields: dict):
        """Como update_issue, pero convierte los bloques {expand} de cualquier
        campo de texto en nodos ADF 'expand' reales tras el guardado."""
        transformed = {}
        expand_titles_by_field = {}
        for name, value in fields.items():
            if isinstance(value, str) and "{expand" in value:
                new_text, titles = self._extract_expand_blocks(value)
                transformed[name] = new_text
                expand_titles_by_field[name] = titles
            else:
                transformed[name] = value

        self.update_issue(issue_key, transformed)

        for name, titles in expand_titles_by_field.items():
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
            response = self.session.get(url, params={"fields": name}, timeout=30)
            self._raise_for_status(response)
            adf = response.json()["fields"].get(name)
            if not adf:
                continue
            adf["content"] = self._replace_markers_with_expand(adf.get("content", []), titles)
            put_response = self.session.put(url, json={"fields": {name: adf}}, timeout=30)
            self._raise_for_status(put_response)

    def add_comment_with_markup(self, issue_key, body: str):
        """Como add_comment, pero convierte los bloques {expand} del
        comentario en nodos ADF 'expand' reales tras publicarlo."""
        if "{expand" not in body:
            return self.add_comment(issue_key, body)

        new_text, titles = self._extract_expand_blocks(body)
        comment = self.add_comment(issue_key, new_text)

        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment/{comment['id']}"
        response = self.session.get(url, timeout=30)
        self._raise_for_status(response)
        adf = response.json()["body"]
        adf["content"] = self._replace_markers_with_expand(adf.get("content", []), titles)
        put_response = self.session.put(url, json={"body": adf}, timeout=30)
        self._raise_for_status(put_response)
        return put_response.json()

    @staticmethod
    def _extract_expand_blocks(wikitext):
        titles = []

        def repl(match):
            idx = len(titles)
            titles.append(match.group("title") or "Ver más")
            body = match.group("body").strip("\n")
            return f"@@JIRA_EXPAND_START_{idx}@@\n\n{body}\n\n@@JIRA_EXPAND_END_{idx}@@"

        return _EXPAND_BLOCK_RE.sub(repl, wikitext), titles

    @staticmethod
    def _node_text(node):
        if node.get("type") != "paragraph":
            return None
        return "".join(c.get("text", "") for c in node.get("content", []) if c.get("type") == "text")

    def _replace_markers_with_expand(self, content, titles):
        new_content = []
        i = 0
        while i < len(content):
            text = self._node_text(content[i])
            match = re.match(r"^@@JIRA_EXPAND_START_(\d+)@@$", text) if text is not None else None
            if not match:
                new_content.append(content[i])
                i += 1
                continue

            idx = int(match.group(1))
            end_marker = f"@@JIRA_EXPAND_END_{idx}@@"
            inner = []
            i += 1
            while i < len(content) and self._node_text(content[i]) != end_marker:
                inner.append(content[i])
                i += 1
            i += 1  # salta el nodo marcador de cierre
            new_content.append({
                "type": "expand",
                "attrs": {"title": titles[idx]},
                "content": inner or [{"type": "paragraph", "content": []}],
            })
        return new_content

    @staticmethod
    def _raise_for_status(response):
        if response.ok:
            return
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise requests.HTTPError(
            f"Error {response.status_code} en la petición a Jira: {detail}"
        )

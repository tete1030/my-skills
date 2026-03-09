#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class OpenCodeClient:
    base_url: str
    token: Optional[str] = None
    timeout: int = 20

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        path = path if path.startswith('/') else '/' + path
        url = self.base_url.rstrip('/') + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += '?' + urlencode(clean)
        return url

    def _headers(self, *, has_body: bool = False) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Any = None,
        expect_json: bool = True,
    ) -> Any:
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        req = Request(
            self._url(path, params),
            headers=self._headers(has_body=payload is not None),
            method=method.upper(),
            data=payload,
        )
        with urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        if not expect_json:
            return None
        if not raw:
            return None
        return json.loads(raw.decode('utf-8'))

    def get_json(self, path: str, **params) -> Any:
        return self.request_json('GET', path, params=params)

    def list_sessions(
        self,
        *,
        directory: Optional[str] = None,
        workspace: Optional[str] = None,
        roots: Optional[bool] = None,
        start: Optional[int] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Any:
        return self.get_json(
            '/session',
            directory=directory,
            workspace=workspace,
            roots=roots,
            start=start,
            search=search,
            limit=limit,
        )

    def get_session(self, session_id: str, *, directory: Optional[str] = None, workspace: Optional[str] = None) -> Any:
        return self.get_json(f'/session/{session_id}', directory=directory, workspace=workspace)

    def create_session(
        self,
        *,
        directory: Optional[str] = None,
        workspace: Optional[str] = None,
        title: Optional[str] = None,
        parent_id: Optional[str] = None,
        permission: Any = None,
    ) -> Any:
        body: Dict[str, Any] = {}
        if parent_id is not None:
            body['parentID'] = parent_id
        if title is not None:
            body['title'] = title
        if permission is not None:
            body['permission'] = permission
        return self.request_json(
            'POST',
            '/session',
            params={'directory': directory, 'workspace': workspace},
            body=body,
        )

    def update_session(
        self,
        session_id: str,
        *,
        directory: Optional[str] = None,
        workspace: Optional[str] = None,
        title: Optional[str] = None,
        archived_at: Optional[int] = None,
    ) -> Any:
        body: Dict[str, Any] = {}
        if title is not None:
            body['title'] = title
        if archived_at is not None:
            body['time'] = {'archived': archived_at}
        return self.request_json(
            'PATCH',
            f'/session/{session_id}',
            params={'directory': directory, 'workspace': workspace},
            body=body,
        )

    def prompt_session(
        self,
        session_id: str,
        *,
        parts: list[dict[str, Any]],
        directory: Optional[str] = None,
        workspace: Optional[str] = None,
        model: Optional[dict[str, str]] = None,
        agent: Optional[str] = None,
        no_reply: Optional[bool] = None,
        system: Optional[str] = None,
        variant: Optional[str] = None,
        asynchronous: bool = False,
    ) -> Any:
        body: Dict[str, Any] = {'parts': parts}
        if model is not None:
            body['model'] = model
        if agent is not None:
            body['agent'] = agent
        if no_reply is not None:
            body['noReply'] = no_reply
        if system is not None:
            body['system'] = system
        if variant is not None:
            body['variant'] = variant
        return self.request_json(
            'POST',
            f'/session/{session_id}/prompt_async' if asynchronous else f'/session/{session_id}/message',
            params={'directory': directory, 'workspace': workspace},
            body=body,
            expect_json=not asynchronous,
        )

    def session_messages(self, session_id: str, limit: int = 20, *, directory: Optional[str] = None, workspace: Optional[str] = None) -> Any:
        return self.get_json(f'/session/{session_id}/message', directory=directory, workspace=workspace, limit=limit)

    def session_todo(self, session_id: str, *, directory: Optional[str] = None, workspace: Optional[str] = None) -> Any:
        return self.get_json(f'/session/{session_id}/todo', directory=directory, workspace=workspace)

    def session_status(self, *, directory: Optional[str] = None, workspace: Optional[str] = None) -> Any:
        return self.get_json('/session/status', directory=directory, workspace=workspace)

    def pty(self, session_id: Optional[str] = None) -> Any:
        params = {"sessionID": session_id} if session_id else {}
        return self.get_json('/pty', **params)

    def permission(self) -> Any:
        return self.get_json('/permission')

    def question(self) -> Any:
        return self.get_json('/question')

    def latest_message(self, session_id: str, *, directory: Optional[str] = None, workspace: Optional[str] = None) -> Any:
        data = self.session_messages(session_id, limit=1, directory=directory, workspace=workspace)
        if isinstance(data, list):
            return data[-1] if data else None
        if isinstance(data, dict):
            items = data.get('items') or data.get('messages') or data.get('data') or []
            return items[-1] if items else data
        return data


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Minimal OpenCode API client for skill prototyping.')
    p.add_argument('--base-url', required=True)
    p.add_argument('--token')
    p.add_argument('--timeout', type=int, default=20)
    sub = p.add_subparsers(dest='cmd', required=True)

    p_sessions = sub.add_parser('sessions')
    p_sessions.add_argument('--directory')
    p_sessions.add_argument('--workspace')
    p_sessions.add_argument('--search')
    p_sessions.add_argument('--limit', type=int)

    p_create = sub.add_parser('create-session')
    p_create.add_argument('--directory')
    p_create.add_argument('--workspace')
    p_create.add_argument('--title')

    p_msg = sub.add_parser('latest-message')
    p_msg.add_argument('--session-id', required=True)
    p_msg.add_argument('--directory')
    p_msg.add_argument('--workspace')

    p_todo = sub.add_parser('todo')
    p_todo.add_argument('--session-id', required=True)
    p_todo.add_argument('--directory')
    p_todo.add_argument('--workspace')

    p_prompt = sub.add_parser('prompt-async')
    p_prompt.add_argument('--session-id', required=True)
    p_prompt.add_argument('--text', required=True)
    p_prompt.add_argument('--directory')
    p_prompt.add_argument('--workspace')

    p_status = sub.add_parser('status')
    p_status.add_argument('--directory')
    p_status.add_argument('--workspace')
    sub.add_parser('permission')
    sub.add_parser('question')

    p_pty = sub.add_parser('pty')
    p_pty.add_argument('--session-id')
    return p


def main() -> None:
    p = build_parser()
    args = p.parse_args()
    client = OpenCodeClient(base_url=args.base_url, token=args.token, timeout=args.timeout)

    if args.cmd == 'sessions':
        out = client.list_sessions(directory=args.directory, workspace=args.workspace, search=args.search, limit=args.limit)
    elif args.cmd == 'create-session':
        out = client.create_session(directory=args.directory, workspace=args.workspace, title=args.title)
    elif args.cmd == 'latest-message':
        out = client.latest_message(args.session_id, directory=args.directory, workspace=args.workspace)
    elif args.cmd == 'todo':
        out = client.session_todo(args.session_id, directory=args.directory, workspace=args.workspace)
    elif args.cmd == 'prompt-async':
        out = client.prompt_session(
            args.session_id,
            directory=args.directory,
            workspace=args.workspace,
            parts=[{'type': 'text', 'text': args.text}],
            asynchronous=True,
        ) or {"accepted": True}
    elif args.cmd == 'status':
        out = client.session_status(directory=args.directory, workspace=args.workspace)
    elif args.cmd == 'permission':
        out = client.permission()
    elif args.cmd == 'question':
        out = client.question()
    elif args.cmd == 'pty':
        out = client.pty(args.session_id)
    else:
        raise SystemExit(f'unsupported command: {args.cmd}')

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

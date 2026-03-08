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

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_json(self, path: str, **params) -> Any:
        req = Request(self._url(path, params), headers=self._headers(), method='GET')
        with urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode('utf-8')
        return json.loads(raw)

    def list_sessions(self) -> Any:
        return self.get_json('/session')

    def session_messages(self, session_id: str, limit: int = 20) -> Any:
        return self.get_json(f'/session/{session_id}/message', limit=limit)

    def session_todo(self, session_id: str) -> Any:
        return self.get_json(f'/session/{session_id}/todo')

    def session_status(self) -> Any:
        return self.get_json('/session/status')

    def pty(self, session_id: Optional[str] = None) -> Any:
        params = {"sessionID": session_id} if session_id else {}
        return self.get_json('/pty', **params)

    def permission(self) -> Any:
        return self.get_json('/permission')

    def question(self) -> Any:
        return self.get_json('/question')

    def latest_message(self, session_id: str) -> Any:
        data = self.session_messages(session_id, limit=1)
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

    sub.add_parser('sessions')

    p_msg = sub.add_parser('latest-message')
    p_msg.add_argument('--session-id', required=True)

    p_todo = sub.add_parser('todo')
    p_todo.add_argument('--session-id', required=True)

    sub.add_parser('status')
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
        out = client.list_sessions()
    elif args.cmd == 'latest-message':
        out = client.latest_message(args.session_id)
    elif args.cmd == 'todo':
        out = client.session_todo(args.session_id)
    elif args.cmd == 'status':
        out = client.session_status()
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

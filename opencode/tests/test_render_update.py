import json
import tempfile
import unittest
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'opencode' / 'scripts' / 'opencode_render_update.py'


class RenderUpdateTests(unittest.TestCase):
    def render(self, payload):
        with tempfile.NamedTemporaryFile('w+', suffix='.json', delete=False) as tmp:
            tmp.write(json.dumps(payload, ensure_ascii=False))
            path = tmp.name
        out = subprocess.run(['python3', str(SCRIPT), '--input', path], capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_completed_without_phase_but_with_preview_mentions_summary(self):
        payload = {
            'decision': {'decision': 'visible_update', 'reason': 'status=completed'},
            'observation': {'status': 'completed', 'phase': None, 'noChange': False},
            'after': {'status': 'completed', 'phase': None},
            'snapshot': {'latestMessage': {'message.lastTextPreview': 'done summary'}}
        }
        text = self.render(payload)
        self.assertIn('任务看起来已完成', text)
        self.assertIn('已提取到最终输出摘要', text)
        self.assertIn('done summary', text)

    def test_running_no_change_without_phase_mentions_running(self):
        payload = {
            'decision': {'decision': 'visible_update', 'reason': 'no_change_age>=0m'},
            'observation': {'status': 'running', 'phase': None, 'noChange': True},
            'after': {'status': 'running', 'phase': None, 'consecutiveNoChangeCount': 3},
            'snapshot': {'latestMessage': {}}
        }
        text = self.render(payload)
        self.assertIn('暂无显著变化', text)
        self.assertIn('当前仍处于运行中', text)
        self.assertIn('连续 no-change 次数：3', text)

    def test_render_from_structured_turn_result(self):
        payload = {
            'factSkeleton': {
                'status': 'blocked',
                'phase': 'Await approval',
                'latestMeaningfulPreview': 'Permission request pending',
                'reason': 'status=blocked',
            },
            'shouldSend': True,
            'delivery': {
                'originSession': 'agent:main:telegram:group:-1003607560565:topic:3348',
                'originTarget': 'telegram:-1003607560565:topic:3348',
            },
            'cadence': {
                'decision': 'visible_update',
                'noChange': False,
                'consecutiveNoChangeCount': 0,
            },
        }
        text = self.render(payload)
        self.assertIn('被阻塞', text)
        self.assertIn('Await approval', text)
        self.assertIn('Permission request pending', text)


if __name__ == '__main__':
    unittest.main()

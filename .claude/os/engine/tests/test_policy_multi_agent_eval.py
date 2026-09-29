"""Local contract checks; SDK calls are mocked, never paid inference."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import policy_multi_agent_eval as m

class MultiAgentEvalTests(unittest.TestCase):
    def result(self, value='A'):
        return dict(value=value, confidence='HIGH', rulesApplied=['P1'], reason='evidence',
                    casesApplied=[], evidenceImageIds=[])

    def test_schema_and_references(self):
        schema = m.output_schema(['A','B'])
        m.validate_result(self.result(), schema, {'P1'}, set(), set())
        with self.assertRaises(m.jsonschema.ValidationError):
            m.validate_result(self.result('Z'), schema)
        with self.assertRaises(ValueError):
            m.validate_result(self.result(), schema, {'P2'}, set(), set())
        for field, value in [('casesApplied', ['unknown-case']), ('evidenceImageIds', ['unknown-image'])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.validate_result({**self.result(), field: value}, schema, {'P1'}, set(), set())

    def test_all_tasks_and_fields_use_the_shared_policy_projection(self):
        attributes = Path(__file__).resolve().parents[2] / 'attributes'
        for profile in sorted(attributes.glob('*/profile.json')):
            spec = json.loads(profile.read_text())['policyTask']
            definitions = profile.parent / 'definitions.md'
            for field in spec['fields']:
                policy, allowed = m.render_policy(definitions, field['id'])
                rule_ids = [r['id'] for r in m.decision_rules(definitions, field['id'])]
                async def fake(root, prompt, model, schema, orchestrate=True):
                    self.assertEqual((root / 'policy.txt').read_text(), policy)
                    self.assertEqual(schema['properties']['value']['enum'], allowed)
                    self.assertEqual(json.loads((root / 'input.json').read_text())['data'], {'title': 'sample'})
                    return dict(is_error=False, structured_output={**self.result(allowed[0]),
                                'rulesApplied': [rule_ids[0]]}, trace=[])
                with self.subTest(task=profile.parent.name, field=field['id']), patch.object(m, 'invoke', fake):
                    record = asyncio.run(m.run({'data': {'title': 'sample'}}, definitions, field['id']))
                    self.assertEqual(record['status'], 'NO_GOLD')
                    self.assertEqual([step['stage'] for step in record['steps']], ['READ'])
                    self.assertEqual(record['policy'], policy)

    def test_role_prompts_share_policy_path_and_configured_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roles = m.agent_definitions(root, 'requested-model')
            self.assertEqual(set(roles), {'reader', 'reviewer', 'precedent'})
            for role in roles.values():
                self.assertIn(str(root / 'policy.txt'), role.prompt)
                self.assertIn(str(root / 'input.json'), role.prompt)
                self.assertEqual(role.model, 'requested-model')
                self.assertEqual(role.tools, ['Read'])

    def execute(self, verdict, retry='B'):
        seen=[]
        async def fake(root,prompt,model,schema,orchestrate=True):
            payload=json.loads((root/'input.json').read_text())
            self.assertEqual(payload['data'], {'title':'test'})
            self.assertNotIn('expected', payload)
            seen.append((prompt,orchestrate))
            output = (dict(verdict=verdict, reason='review', feedback='check P1', evidenceImageIds=[])
                      if not orchestrate else self.result(retry if len(seen)==3 else 'A'))
            return dict(is_error=False, structured_output=output, trace=[])
        with patch.object(m,'invoke',fake), patch.object(m,'render_policy',return_value=('policy',['A','B'])), \
             patch.object(m,'decision_rules',return_value=[{'id':'P1'}]):
            result=asyncio.run(m.run({'data':{'title':'test'},'expected':'B','goldSource':'secret-gold'},
                                    Path('unused'),'field'))
        return result,seen

    def test_retry_is_fresh_and_has_no_expected(self):
        result,seen=self.execute('EXTRACT_ERROR')
        self.assertEqual(result['status'],'MATCH_AFTER_RETRY')
        self.assertEqual([x[1] for x in seen],[True,False,True])
        self.assertNotIn('기존 정답',seen[0][0])
        self.assertNotIn('기존 정답',seen[2][0])

    def test_retry_only_once(self):
        result,seen=self.execute('EXTRACT_ERROR',retry='A')
        self.assertEqual(result['status'],'HUMAN_REVIEW')
        self.assertEqual(len(seen),3)

    def test_gold_suspect_and_policy_gap_do_not_retry(self):
        for verdict in ('GT_SUSPECT','POLICY_GAP'):
            result,seen=self.execute(verdict)
            self.assertEqual(result['status'],verdict)
            self.assertEqual(len(seen),2)

if __name__=='__main__':
    unittest.main()

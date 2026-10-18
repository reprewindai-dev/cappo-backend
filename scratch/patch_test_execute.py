import re
from pathlib import Path

path = Path('tests/capability_mount/test_execute_consequence.py')
content = path.read_text(encoding='utf-8')

content = content.replace('def dispatch(self, context: ConsequenceContext) -> object:', 'def execute_translation(self, translation: ProviderTranslation) -> object:')
content = content.replace('result = super().dispatch(context)', 'result = super().execute_translation(translation)')

content = re.sub(r'ConsequenceContext,?\s*', '', content)
if 'ProviderTranslation' not in content:
    content = content.replace('TargetAdapterRegistry,', 'TargetAdapterRegistry, ProviderTranslation,')

path.write_text(content, encoding='utf-8')
print('Patched test_execute_consequence.py')

path = Path('tests/capability_mount/test_g0a7_clean_node.py')
content = path.read_text(encoding='utf-8')
content = content.replace('def dispatch(self, context: ConsequenceContext) -> object:', 'def execute_translation(self, translation: ProviderTranslation) -> object:')
content = content.replace('super().dispatch(context)', 'super().execute_translation(translation)')
content = re.sub(r'ConsequenceContext,?\s*', '', content)
if 'ProviderTranslation' not in content:
    content = content.replace('LocalRecordAdapter,', 'LocalRecordAdapter, ProviderTranslation,')
path.write_text(content, encoding='utf-8')
print('Patched test_g0a7_clean_node.py')

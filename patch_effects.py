import pathlib
path = pathlib.Path('cappo_backend/capability_mount/effects.py')
text = path.read_text()
text = text.replace('EffectAdapter', 'TargetAdapter')
text = text.replace('EffectTargetRegistry', 'TargetAdapterRegistry')
text = text.replace('invalid_effect_resource', 'invalid_target_resource')
text = text.replace('effect_not_mapped', 'target_not_mapped')
path.write_text(text)

"""Print an exact distribution inventory, including build tools, without paths."""
import importlib.metadata

for name, version in sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()):
    print(f'{name}=={version}')

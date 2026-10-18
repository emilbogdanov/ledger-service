import os

from hypothesis import settings

# CI searches harder than a local run, and shared runners are slow enough to trip the
# per-example deadline on an unlucky example. The blob makes a CI failure reproducible locally.
settings.register_profile("ci", max_examples=500, deadline=None, print_blob=True)
settings.load_profile("ci" if os.environ.get("CI") else "default")

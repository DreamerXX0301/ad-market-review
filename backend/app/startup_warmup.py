"""Load mandatory read dependencies before the API accepts its first request.

Only Python/native modules are imported. No market objects, business files,
database connections, provider requests, tasks or worker processes are opened.
The existing mandatory-dependency failure therefore prevents API readiness.
"""
from importlib import import_module


def warmup_read_dependencies():
    # Parquet's first filtered read otherwise lazily loads both packages inside
    # the user's request. Import caching makes later lifespan entries harmless.
    import_module('pandas')
    import_module('pyarrow.dataset')

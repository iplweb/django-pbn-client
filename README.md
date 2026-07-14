# django-pbn-client

[![CI](https://github.com/iplweb/django-pbn-client/actions/workflows/ci.yml/badge.svg)](https://github.com/iplweb/django-pbn-client/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.14-blue.svg)](https://www.python.org/)

Reusable Django models and persistence services for data downloaded from the
Polish Bibliography Network (PBN).

The package deliberately does not define concrete application models or
migrations. Applications subclass `BasePBNMongoDBModel` and retain ownership
of their database schema:

```python
from django.db import models
from django_pbn_client import BasePBNMongoDBModel


class Publication(BasePBNMongoDBModel):
    title = models.TextField(blank=True, default="")
    pull_up_on_save = ["title"]
```

Downloaded objects can then be stored atomically:

```python
from django_pbn_client import download_pbn_objects, upsert_pbn_object

publication = upsert_pbn_object(payload, Publication)
download_pbn_objects(client.get_publications(), Publication)
```

`django-pbn-client` depends only on Django and the transport-level
`pbn-client` package. Progress bars, background jobs, concrete relationships,
and application-specific integration remain the responsibility of the host
application.

Concurrent page downloads use threads by default. The optional
`method="processes"` mode uses the POSIX `fork` start method and is therefore
not available on Windows.

## Installation

```console
pip install django-pbn-client
```

## Development

This package depends on the transport-level
[`pbn-client`](https://github.com/iplweb/pbn-client) package. Until `pbn-client`
is published to PyPI, a dev-only `[tool.uv.sources]` entry in `pyproject.toml`
resolves it from a sibling checkout, so clone both repositories side by side:

```console
git clone https://github.com/iplweb/pbn-client.git
git clone https://github.com/iplweb/django-pbn-client.git
cd django-pbn-client
```

The source override is ignored when building or publishing wheels, so end users
still get `pbn-client` from PyPI via the version constraint in
`[project.dependencies]`.

Run the standalone package tests with:

```console
uv run --group dev pytest
```

## License

Released under the [MIT License](LICENSE).

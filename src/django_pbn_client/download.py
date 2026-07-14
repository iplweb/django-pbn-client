"""High-level facade for downloading PBN resources into Django models."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, NamedTuple

from django_pbn_client.pages import ThreadedModelSaver, download_pages
from django_pbn_client.persistence import download_pbn_objects, upsert_pbn_object

logger = logging.getLogger(__name__)

_SEQUENTIAL_CONCURRENCY = (None, 1, "sequential")


class DownloadResult(NamedTuple):
    """Outcome of :func:`download_to_model`.

    ``processed`` counts elements whose ``save`` callback returned without
    raising; ``errored`` counts elements whose ``save`` raised. The default
    :func:`~django_pbn_client.persistence.upsert_pbn_object` does not report
    whether it created, updated or skipped a row, so no finer-grained
    created/updated/skipped counts can be offered here.
    """

    processed: int
    errored: int


class _ElementCounter:
    """Thread-safe wrapper that counts successes and logged failures."""

    def __init__(self, save: Callable[..., Any], client) -> None:
        self._save = save
        self._client = client
        self._lock = threading.Lock()
        self.processed = 0
        self.errored = 0

    def __call__(self, element, model_class, client=None) -> None:
        del client  # the facade-level client is authoritative
        try:
            self._save(element, model_class, client=self._client)
        except Exception:
            logger.exception(
                "Saving a PBN element into %r failed; continuing with the "
                "remaining elements",
                model_class,
            )
            with self._lock:
                self.errored += 1
        else:
            with self._lock:
                self.processed += 1

    def result(self) -> DownloadResult:
        return DownloadResult(processed=self.processed, errored=self.errored)


def download_to_model(
    resource_factory: Callable[[], Any],
    model_class,
    *,
    save: Callable[..., Any] = upsert_pbn_object,
    concurrency=None,
    progress=None,
    client=None,
) -> DownloadResult:
    """Download every element of a PBN resource into ``model_class``.

    This is a thin facade over the existing downloaders: paging and
    persistence are delegated wholesale to
    :func:`~django_pbn_client.persistence.download_pbn_objects` (sequential)
    or :func:`~django_pbn_client.pages.download_pages` (threaded).

    ``resource_factory`` is a zero-argument callable returning a fresh
    paginator/resource. A factory is required — not a pre-built resource —
    because constructing a real PBN pageable resource already performs the
    first request, and a factory leaves room for future retry support.

    ``concurrency`` selects the delegate: ``None``, ``1`` or ``"sequential"``
    iterate elements in this thread; an ``int > 1`` runs that many worker
    threads; ``"threads"`` runs the delegate's default worker count.

    ``progress`` is passed through verbatim: the sequential delegate calls it
    with ``(elements, total_elements, label)``, the threaded delegate with
    ``(pages, total_pages, label)``.

    Elements whose ``save`` raises are logged and counted in
    ``DownloadResult.errored``; the download continues. ``save`` results are
    not inspected, so created/updated/skipped cannot be distinguished (the
    default ``upsert_pbn_object`` returns an instance in all three cases).
    """
    threaded_workers = _resolve_concurrency(concurrency)
    resource = resource_factory()
    counter = _ElementCounter(save, client)

    if threaded_workers is None:
        download_pbn_objects(
            resource,
            model_class,
            save=counter,
            client=client,
            progress=progress,
        )
        return counter.result()

    bound_model_class = model_class

    class _BoundSaver(ThreadedModelSaver):
        model_class = bound_model_class
        save_function = staticmethod(counter)

    kwargs = {} if threaded_workers == "default" else {"workers": threaded_workers}
    download_pages(
        client,
        resource,
        getter_class=_BoundSaver,
        progress=progress,
        **kwargs,
    )
    return counter.result()


def _resolve_concurrency(concurrency):
    """Map ``concurrency`` onto ``None`` (sequential) or a worker count."""
    if concurrency in _SEQUENTIAL_CONCURRENCY:
        return None
    if concurrency == "threads":
        return "default"
    if isinstance(concurrency, int) and not isinstance(concurrency, bool):
        if concurrency > 1:
            return concurrency
    raise ValueError(
        "Unsupported concurrency: expected None, 1, 'sequential', "
        f"an int > 1 or 'threads', got {concurrency!r}"
    )
